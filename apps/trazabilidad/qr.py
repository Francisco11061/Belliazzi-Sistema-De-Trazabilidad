from io import BytesIO

import qrcode
from qrcode.image.pure import PyPNGImage
from django.urls import reverse


def construir_url_qr_caja(request, caja):
    return request.build_absolute_uri(reverse(
        "producto_terminado:consulta_caja_qr", kwargs={"identificador": caja.identificador_qr},
    ))


def generar_png_qr_caja(request, caja):
    imagen = qrcode.make(construir_url_qr_caja(request, caja), image_factory=PyPNGImage, box_size=8, border=4)
    contenido = BytesIO()
    imagen.save(contenido)
    return contenido.getvalue()
