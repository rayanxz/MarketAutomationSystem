from django.db import models


class PublicIdSequence(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    next_value = models.BigIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "core_publicidsequence"

    def __str__(self) -> str:
        return f"{self.key}:{self.next_value}"
