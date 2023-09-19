import json

from config.env import env

USE_FILES = env.bool('SCHEMA_API_USE_FILES', False)
S3 = {
    'URL': env.url('SCHEMA_API_S3_URL', None),
    'ACCESS_KEY_ID': env.url('SCHEMA_API_S3_ACCESS_KEY_ID', None),
    'SECRET_ACCESS_KEY': env.url('SCHEMA_API_SECRET_ACCESS_KEY', None),
    'VALIDITY_PERIOD_SECONDS': env.int('SCHEMA_API_S3_VALIDITY_PERIOD_SECONDS', 24 * 60 * 60),
    'MAX_PART_SIZE_BYTES': env.int('SCHEMA_API_S3_MAX_PART_SIZE_BYTES', 100 * 1024 * 1024),
    'CLIENT_PARAMETERS': json.loads(env.json('SCHEMA_API_S3_CLIENT_PARAMETERS', '{}'))
}
