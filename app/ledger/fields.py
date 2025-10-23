from django.db import models
from django.core.serializers.json import DjangoJSONEncoder
import json

class JSONTextField(models.TextField):
    """
    Store JSON as text, but read/write Python dicts/lists transparently.
    Works on SQLite without JSON1, and on any other backend.
    """

    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value)
        except Exception:
            return value  # fall back to raw text if it's not valid JSON

    def to_python(self, value):
        if value is None or isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value)
        except Exception:
            return value

    def get_prep_value(self, value):
        if value is None:
            return None
        # Ensure we always store a JSON string
        return json.dumps(value, cls=DjangoJSONEncoder)
