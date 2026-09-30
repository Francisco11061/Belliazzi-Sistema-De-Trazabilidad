from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.views.decorators.debug import sensitive_variables

from .models import EventoUsuario, Rol, Usuario
from .permisos import ROL_ENCARGADA, ROL_JEFE, ROL_OPERARIA, tiene_rol

ROLES_FUNCIONALES = (ROL_JEFE, ROL_ENCARGADA, ROL_OPERARIA)


def roles_disponibles():
    return Rol.objects.filter(codigo__in=ROLES_FUNCIONALES, activo=True)


def _bloquear_gestion(actor):
    # Un orden común de bloqueo serializa altas/cambios de estado y de rol,
    # incluso cuando dos jefes intentan quitarse el rol simultáneamente.
    list(Rol.objects.select_for_update().order_by("pk"))
    if not actor.is_authenticated:
        raise PermissionDenied
    actor = Usuario.objects.select_for_update().get(pk=actor.pk)
    if not actor.is_active or not tiene_rol(actor, ROL_JEFE):
        raise PermissionDenied
    if actor.cambio_contrasena_pendiente and not actor.is_superuser:
        raise PermissionDenied
    return actor


def _destino(usuario):
    usuario = Usuario.objects.select_for_update().get(pk=usuario.pk)
    if usuario.is_superuser or usuario.is_staff:
        raise ValidationError("Las cuentas técnicas se administran desde Django Admin.")
    return usuario


def _rol(rol):
    try:
        return roles_disponibles().get(pk=rol.pk)
    except (Rol.DoesNotExist, AttributeError):
        raise ValidationError("El rol seleccionado no está disponible.")


def _nombre_rol(rol):
    return f"{rol.nombre} ({rol.codigo})" if rol else ""


def _proteger_ultimo_jefe(usuario):
    if usuario.is_active and usuario.rol.codigo == ROL_JEFE:
        if not Usuario.objects.filter(
            is_active=True, is_superuser=False, rol__codigo=ROL_JEFE
        ).exclude(pk=usuario.pk).exists():
            raise ValidationError("Debe existir al menos un usuario Jefe activo.")


@transaction.atomic
@sensitive_variables()
def crear_usuario(*, actor, username, first_name, last_name, rol, password):
    actor = _bloquear_gestion(actor)
    usuario = Usuario(
        username=Usuario.normalize_username(username.strip()),
        first_name=first_name, last_name=last_name, rol=_rol(rol),
        is_active=True, is_staff=False, is_superuser=False,
        cambio_contrasena_pendiente=True, creado_por=actor,
    )
    if Usuario.objects.filter(username=usuario.username).exists():
        raise ValidationError("El nombre de usuario ya está en uso.")
    validate_password(password, usuario)
    usuario.set_password(password)
    usuario.full_clean()
    try:
        with transaction.atomic():
            usuario.save()
    except IntegrityError:
        raise ValidationError("El nombre de usuario ya está en uso.")
    EventoUsuario.objects.create(usuario=usuario, actor=actor, tipo=EventoUsuario.Tipo.CREACION,
                                 rol_nuevo=_nombre_rol(usuario.rol))
    return usuario


@transaction.atomic
def cambiar_rol(*, actor, usuario, rol):
    actor = _bloquear_gestion(actor)
    usuario = _destino(usuario)
    rol = _rol(rol)
    if usuario.rol_id == rol.pk:
        return usuario
    if rol.codigo != ROL_JEFE:
        _proteger_ultimo_jefe(usuario)
    anterior = _nombre_rol(usuario.rol)
    usuario.rol = rol
    usuario.save(update_fields=["rol"])
    EventoUsuario.objects.create(usuario=usuario, actor=actor, tipo=EventoUsuario.Tipo.ROL,
                                 rol_anterior=anterior, rol_nuevo=_nombre_rol(rol))
    return usuario


@transaction.atomic
def cambiar_estado(*, actor, usuario, activo):
    actor = _bloquear_gestion(actor)
    usuario = _destino(usuario)
    if not activo and actor.pk == usuario.pk:
        raise ValidationError("No puede desactivar su propia cuenta.")
    if usuario.is_active == activo:
        return usuario
    if not activo:
        _proteger_ultimo_jefe(usuario)
    usuario.is_active = activo
    usuario.save(update_fields=["is_active"])
    EventoUsuario.objects.create(
        usuario=usuario, actor=actor,
        tipo=EventoUsuario.Tipo.REACTIVACION if activo else EventoUsuario.Tipo.DESACTIVACION,
    )
    return usuario


@transaction.atomic
@sensitive_variables()
def restablecer_contrasena(*, actor, usuario, password):
    actor = _bloquear_gestion(actor)
    usuario = _destino(usuario)
    if actor.pk == usuario.pk:
        raise ValidationError("Use el cambio de contraseña de su propia cuenta.")
    if usuario.check_password(password):
        raise ValidationError("La nueva contraseña debe ser distinta de la actual.")
    validate_password(password, usuario)
    usuario.set_password(password)
    usuario.cambio_contrasena_pendiente = True
    usuario.save(update_fields=["password", "cambio_contrasena_pendiente"])
    EventoUsuario.objects.create(usuario=usuario, actor=actor, tipo=EventoUsuario.Tipo.RESET)
    return usuario


@transaction.atomic
@sensitive_variables()
def cambiar_contrasena_propia(*, usuario, anterior, nueva):
    usuario = Usuario.objects.select_for_update().get(pk=usuario.pk)
    if not usuario.is_active or not usuario.check_password(anterior):
        raise ValidationError("La contraseña actual no es correcta.")
    if usuario.check_password(nueva):
        raise ValidationError("La nueva contraseña debe ser distinta de la actual.")
    validate_password(nueva, usuario)
    usuario.set_password(nueva)
    usuario.cambio_contrasena_pendiente = False
    usuario.save(update_fields=["password", "cambio_contrasena_pendiente"])
    EventoUsuario.objects.create(usuario=usuario, actor=usuario, tipo=EventoUsuario.Tipo.CONTRASENA)
    return usuario
