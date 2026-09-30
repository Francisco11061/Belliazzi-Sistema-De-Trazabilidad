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


class EventosUsuarioQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("El historial de usuarios no se puede modificar.")

    def delete(self):
        raise ValueError("El historial de usuarios no se puede eliminar.")


class EventoUsuario(models.Model):
    class Tipo(models.TextChoices):
        CREACION = "CREACION", "Usuario creado"
        ROL = "ROL", "Rol cambiado"
        RESET = "RESET", "Contraseña restablecida"
        DESACTIVACION = "DESACTIVACION", "Usuario desactivado"
        REACTIVACION = "REACTIVACION", "Usuario reactivado"
        CONTRASENA = "CONTRASENA", "Contraseña cambiada por el usuario"

    usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name="eventos")
    actor = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name="eventos_realizados")
    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    fecha_hora = models.DateTimeField(auto_now_add=True)
    rol_anterior = models.CharField(max_length=80, blank=True)
    rol_nuevo = models.CharField(max_length=80, blank=True)

    objects = EventosUsuarioQuerySet.as_manager()

    class Meta:
        ordering = ["-fecha_hora", "-pk"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("El historial de usuarios no se puede modificar.")
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("El historial de usuarios no se puede eliminar.")
