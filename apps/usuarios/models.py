from django.db import models
from django.contrib.auth.models import AbstractUser
from django.db.models import Q


class Rol(models.Model):
    codigo = models.CharField(max_length=20, unique=True)
    nombre = models.CharField(max_length=50, unique=True)
    descripcion = models.CharField(max_length=150, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Usuario(AbstractUser):
    rol = models.ForeignKey(
        Rol,
        on_delete=models.PROTECT,
        related_name="usuarios",
        null=True,
        blank=True,
    )

    cambio_contrasena_pendiente = models.BooleanField(default=False)

    creado_por = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="usuarios_creados",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(is_superuser=True) | Q(rol__isnull=False),
                name="usuario_normal_debe_tener_rol",
            )
        ]

    def __str__(self):
        return self.get_full_name() or self.username