from django import forms
from django.contrib.auth.forms import PasswordChangeForm, UsernameField
from django.contrib.auth.password_validation import validate_password, password_validators_help_text_html

from .models import Usuario
from .services import roles_disponibles


class EstiloFormulario:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "block w-full min-w-0 rounded-lg border border-slate-300 bg-white "
                "px-3 py-2.5 text-sm focus:border-cyan-700 focus:outline-cyan-700"
            )


class ContrasenaTemporalForm(EstiloFormulario, forms.Form):
    password1 = forms.CharField(label="Contraseña temporal", strip=False,
                               widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
                               help_text=password_validators_help_text_html())
    password2 = forms.CharField(label="Confirmar contraseña temporal", strip=False,
                               widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def __init__(self, *args, usuario=None, **kwargs):
        self.usuario = usuario
        super().__init__(*args, **kwargs)
        self.fields["password1"].help_text = password_validators_help_text_html()

    def clean(self):
        data = super().clean()
        password = data.get("password1")
        if password and password != data.get("password2"):
            self.add_error("password2", "Las contraseñas no coinciden.")
        if password:
            usuario = self.usuario or Usuario(**{
                key: data.get(key, "") for key in ("username", "first_name", "last_name")
            })
            try:
                validate_password(password, usuario)
            except forms.ValidationError as error:
                self.add_error("password1", error)
        return data


class CrearUsuarioForm(ContrasenaTemporalForm):
    username = UsernameField(label="Nombre de usuario", max_length=150,
                             validators=Usuario._meta.get_field("username").validators)
    first_name = forms.CharField(label="Nombre", max_length=150)
    last_name = forms.CharField(label="Apellido", max_length=150)
    rol = forms.ModelChoiceField(label="Rol", queryset=roles_disponibles())
    field_order = ["username", "first_name", "last_name", "rol", "password1", "password2"]

    def clean_username(self):
        username = self.cleaned_data["username"]
        if Usuario.objects.filter(username=username).exists():
            raise forms.ValidationError("El nombre de usuario ya está en uso.")
        return username


class RolUsuarioForm(EstiloFormulario, forms.Form):
    rol = forms.ModelChoiceField(label="Nuevo rol", queryset=roles_disponibles())


class CambioContrasenaForm(EstiloFormulario, PasswordChangeForm):
    pass
