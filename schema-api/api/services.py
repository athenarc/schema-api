import datetime
import uuid
from typing import Iterable

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from api import taskapis
from api.constants import TaskStatus
from api.models import Task, Executor, Env, MountPoint, Volume, ResourceSet, ExecutorOutputLog, Context, \
    Participation, StatusHistoryPoint, Tag
from api_auth.constants import AuthEntityType
from api_auth.models import AuthEntity
from quotas.evaluators import ActiveResourcesDbQuotasEvaluator, RequestedResourcesQuotasEvaluator, TasksQuotasEvaluator
from quotas.models import ContextQuotas
from quotas.services import QuotasService
from util.exceptions import ApplicationError, ApplicationErrorHelper, ApplicationNotFoundError, \
    ApplicationValidationError


class UserContextService:

    def __init__(self, auth_entity: AuthEntity):
        self.auth_entity = auth_entity

    def list_contexts(self) -> Iterable[Context]:
        return self.auth_entity.contexts.all()

    def retrieve_context(self, name: str) -> Context:
        try:
            return self.auth_entity.contexts.get(name=name)
        except Context.DoesNotExist as dne:
            raise ApplicationNotFoundError from dne


class TaskService:

    def __init__(self, context=None, auth_entity=None):
        self.context = context
        self.auth_entity = auth_entity

    @transaction.atomic
    def submit_task(self, *, executors: Iterable[Executor], **optional):
        input_mount_points = optional.pop('inputs', None)
        output_mount_points = optional.pop('outputs', None)
        volumes = optional.pop('volumes', None)
        tags = optional.pop('tags', None)
        resource_set = optional.pop('resources', None)

        task = Task.objects.create(context=self.context, user=self.auth_entity, **optional)

        status_history_point_service = StatusHistoryPointService(task)
        status_history_point_service.update_status(TaskStatus.SUBMITTED)

        i = 0
        for executor in executors:
            envs = executor.pop('envs', None)

            i += 1
            order = i
            executor = Executor.objects.create(task=task, order=order, **executor)

            if envs:
                for kv_pair in envs:
                    Env.objects.create(executor=executor, key=kv_pair['key'], value=kv_pair['value'])

        if input_mount_points:
            for mount_point in input_mount_points:
                MountPoint.objects.create(task=task, is_input=True, **mount_point)
        if output_mount_points:
            for mount_point in output_mount_points:
                MountPoint.objects.create(task=task, is_input=False, **mount_point)
        if volumes:
            for volume_path in volumes:
                Volume.objects.create(task=task, path=volume_path)

        if resource_set:
            ResourceSet.objects.create(task=task, **resource_set)
        else:
            ResourceSet.objects.create(task=task)

        if tags:
            tag_set = set(tags)
            for tag in tag_set:
                temp_tag = Tag.objects.get_or_create(value=tag)[0]
                task.tags.add(temp_tag)

        quotas_service = QuotasService(task.context, task.user)
        context_quotas, participation_quotas = quotas_service.get_qualified_quotas()
        RequestedResourcesQuotasEvaluator.evaluate(context_quotas, participation_quotas, task)
        TasksQuotasEvaluator.evaluate(context_quotas, participation_quotas, task)
        ActiveResourcesDbQuotasEvaluator.evaluate(context_quotas, participation_quotas, task)

        status_history_point_service.update_status(TaskStatus.APPROVED)

        if settings.TASK_API["TASK_API_CLASS"] and not settings.DISABLE_TASK_SCHEDULING:
            task_api_class = taskapis.get_task_api_class()
            task_api = task_api_class(auth_entity=self.auth_entity)

            task_id = task_api.create_task(task)

            task.task_id = task_id
            task.latest_update = timezone.now()
            task.save()

            status_history_point_service.update_status(TaskStatus.SCHEDULED)
        return task

    @transaction.atomic
    def _update_task_by_task_info(self, task, task_info):
        task_status = task_info['status']
        if task_status in [TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELED]:
            task.pending = False

        status_history_point_service = StatusHistoryPointService(task)
        status_history_point_service.update_status(task_status)

        executors_stderr = task_info['stderr'] if 'stderr' in task_info else ['']
        executors_stdout = task_info['stdout'] if 'stdout' in task_info else ['']
        n_executors_logged = len(executors_stdout)
        task_executors = task.executors.all().order_by('order')[:n_executors_logged].prefetch_related(
            'executoroutputlog'
        )
        for i in range(n_executors_logged):
            task_executor = task_executors[i]
            executor_stdout = executors_stdout[i]
            executor_stderr = executors_stderr[i]
            try:
                executor_output_log = ExecutorOutputLog.objects.get(executor=task_executor)
            except ExecutorOutputLog.DoesNotExist:
                executor_output_log = ExecutorOutputLog()
            executor_output_log.stdout = executor_stdout
            executor_output_log.stderr = executor_stderr
            executor_output_log.executor = task_executor
            executor_output_log.save()

        task.save()

    @transaction.atomic
    def _check_if_update_task(self, task):
        
        if task.pending and  task.latest_update is not None and \
                not settings.DISABLE_TASK_SCHEDULING and \
                (task.latest_update - timezone.now()).seconds > settings.TASK_API['DB_TASK_STATUS_TTL_SECONDS']:
            task_api_class = taskapis.get_task_api_class()
            task_api = task_api_class()

            task_info = task_api.get_task_info(task.task_id)

            task_status = task_info['status']
            if task_status in [TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELED]:
                task.pending = False

            status_history_point_service = StatusHistoryPointService(task)
            status_history_point_service.update_status(task_status)

            executors_stderr = task_info['stderr'] if 'stderr' in task_info else ['']
            executors_stdout = task_info['stdout'] if 'stdout' in task_info else ['']
            n_executors_logged = len(executors_stdout)
            task_executors = task.executors.all().order_by('order')[:n_executors_logged].prefetch_related(
                'executoroutputlog'
            )
            for i in range(n_executors_logged):
                task_executor = task_executors[i]
                executor_stdout = executors_stdout[i]
                executor_stderr = executors_stderr[i]
                try:
                    executor_output_log = ExecutorOutputLog.objects.get(executor=task_executor)
                except ExecutorOutputLog.DoesNotExist:
                    executor_output_log = ExecutorOutputLog()
                executor_output_log.stdout = executor_stdout
                executor_output_log.stderr = executor_stderr
                executor_output_log.executor = task_executor
                executor_output_log.save()

            task.save()
        return task

    def _check_if_update_tasks(self):
        if not settings.DISABLE_TASK_SCHEDULING:
            task_api_class = taskapis.get_task_api_class()
            task_api = task_api_class()

            tasks_content = task_api.get_tasks()

            task_data_index = {t['task_id']: t for t in tasks_content}

            db_tasks = Task.objects.filter(task_id__in=task_data_index, pending=True)
            db_tasks_data_mappings = {t.task_id: (t, task_data_index[t.task_id]) for t in db_tasks}

            for tup in db_tasks_data_mappings.values():
                self._update_task_by_task_info(*tup)

    def get_task(self, task_uuid: uuid.UUID):
        try:
            task = Task.objects.get(context=self.context, uuid=task_uuid)
        except Task.DoesNotExist as dne:
            raise ApplicationNotFoundError(f'No task was found with UUID "{task_uuid}"') from dne

        task = self._check_if_update_task(task)

        return task

    def get_task_stdout(self, task_uuid: uuid.UUID):
        task = Task.objects.get(context=self.context, uuid=task_uuid)
        task = self._check_if_update_task(task)

        executors_stdout = [
            executor_output_log.stdout for executor_output_log in ExecutorOutputLog.objects.filter(executor__task=task)
        ]
        return executors_stdout

    def get_task_stderr(self, task_uuid: uuid.UUID):
        task = Task.objects.get(context=self.context, uuid=task_uuid)
        task = self._check_if_update_task(task)

        executors_stderr = [
            executor_output_log.stderr for executor_output_log in ExecutorOutputLog.objects.filter(executor__task=task)
        ]
        return executors_stderr

    def get_tasks(self) -> QuerySet[Task]:
        if settings.UPDATE_STATE_ON_TASKS_LISTING:
            self._check_if_update_tasks()
        return Task.objects.filter(context=self.context)

    def cancel_task(self, task_uuid: uuid.UUID) -> None:
        task = self.get_task(task_uuid)

        if task.pending:

            task_id = task.task_id

            if task_id and settings.TASK_API["TASK_API_CLASS"] and not settings.DISABLE_TASK_SCHEDULING:
                task_api_class = taskapis.get_task_api_class()
                task_api = task_api_class(auth_entity=self.auth_entity)

                if not task_api.cancel(task_id):
                    raise ApplicationValidationError({'uuid': f'Task with UUID \'{task_uuid}\' has already terminated'})

            task.pending = False
            task.save()

            status_history_point_service = StatusHistoryPointService(task)
            status_history_point_service.update_status(TaskStatus.CANCELED)
            return

        raise ApplicationValidationError({'uuid': f'Task with UUID \'{task_uuid}\' has already terminated'})


class StatusHistoryPointService:

    def __init__(self, task: Task):
        self.task = task

    def update_status(self, status: TaskStatus, update_time: datetime.datetime = None) -> StatusHistoryPoint:
        update_time = update_time or timezone.now()
        return StatusHistoryPoint.objects.create(task=self.task, status=status, created_at=update_time)

    def get_status_history(self) -> QuerySet[StatusHistoryPoint]:
        return StatusHistoryPoint.objects.filter(task=self.task).order_by('-update_time')

    def get_current_status(self) -> StatusHistoryPoint:
        return self.get_status_history().first()


class ParticipationService:

    def __init__(self, context: Context):
        self.context = context

    def add_to_context(self, user: AuthEntity) -> Participation:
        if user.entity_type != AuthEntityType.USER:
            raise ApplicationError(f'Only {AuthEntityType.USER} type AuthEntities can be added to a context')

        if user.parent != self.context.owner:
            raise ApplicationError(f'Referenced user\'s parent is different from the context\'s application service')

        participation = Participation(user=user, context=self.context)
        try:
            participation.full_clean()
        except ValidationError as ve:
            raise ApplicationErrorHelper.to_application_error(ve)
        participation.save()
        return participation

    def get_participations(self) -> QuerySet[Participation]:
        return Participation.objects.filter(context=self.context)

    def get_participation(self, auth_entity: AuthEntity) -> Participation:
        try:
            return self.get_participations().get(user=auth_entity)
        except Participation.DoesNotExist:
            raise ApplicationNotFoundError(
                f'No participation exists for user {auth_entity.username} in context {self.context.name}')

    def remove_from_context(self, user: AuthEntity):
        participation = self.get_participation(user)
        participation.delete()


class ContextService:

    def __init__(self, application_service: AuthEntity):
        if application_service.entity_type != AuthEntityType.APPLICATION_SERVICE:
            raise ApplicationError(
                f'{self.__class__.__name__} depends on an AuthEntity of type {AuthEntityType.APPLICATION_SERVICE}'
            )

        self.application_service = application_service

    @transaction.atomic
    def create_context(self, *, name: str):
        # slugify name?

        context = Context(owner=self.application_service, name=name)
        try:
            context.full_clean()
        except ValidationError as ve:
            raise ApplicationErrorHelper.to_application_error(ve)
        context.save()

        context_quotas = ContextQuotas(context=context)
        context_quotas.save()

        return context

    def get_contexts(self) -> QuerySet:
        return Context.objects.filter(owner=self.application_service)

    def get_context(self, *, name: str) -> Context:
        try:
            return self.get_contexts().get(name=name)
        except Context.DoesNotExist:
            raise ApplicationNotFoundError(f'No context exists with name {name}')

    @transaction.atomic
    def update_context(self, *, update_values: dict, context: Context = None, name: str = None) -> Context:
        if not context:
            context = self.get_context(name=name)
        else:
            if context.owner != self.application_service:
                raise ApplicationError(
                    f'Referenced context is owned by a different {AuthEntityType.APPLICATION_SERVICE} AuthEntity from '
                    f'the one used in this ContextService.'
                )

        quotas_update_values = update_values.pop('quotas', None)

        if quotas_update_values:
            context_quotas_service = ContextQuotasService(context)
            context_quotas_service.update_context_quotas(update_values=quotas_update_values)
        return context

    def assign_user(self, user: AuthEntity, context: Context = None, name: str = None) -> Context:
        if not context:
            context = self.get_context(name=name)
        else:
            if context.owner != self.application_service:
                raise ApplicationError(
                    f'Referenced context is owned by a different {AuthEntityType.APPLICATION_SERVICE} AuthEntity from '
                    f'the one used in this ContextService.'
                )

        participation_service = ParticipationService(context)
        participation_service.add_to_context(user)
        return context

    def remove_user(self, user: AuthEntity, context: Context = None, name: str = None) -> Context:
        if not context:
            context = self.get_context(name=name)
        else:
            if context.owner != self.application_service:
                raise ApplicationError(
                    f'Referenced context is owned by a different {AuthEntityType.APPLICATION_SERVICE} AuthEntity from '
                    f'the one used in this ContextService.'
                )

        participation_service = ParticipationService(context)
        participation_service.remove_from_context(user)
        return context
