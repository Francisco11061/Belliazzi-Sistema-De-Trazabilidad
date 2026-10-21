from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse
from apps.trazabilidad.selectors import ADVERTENCIA_PESO_SUPERIOR
from apps.trazabilidad.test_qr_cajas import DatosQR


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class MicrocopyAvisosTests(DatosQR, TestCase):
    def setUp(self):
        self.preparar_qr()
        self.client.force_login(self.jefe)

    def test_reportes_conserva_aviso_oficial_y_remite_al_excel(self):
        respuesta = self.client.get(reverse('reportes:sernapesca'))
        self.assertContains(respuesta, 'Reporte de apoyo. No reemplaza las declaraciones oficiales ante Sernapesca.')
        self.assertContains(respuesta, 'Los criterios de lectura están en la hoja Resumen del Excel.')
        self.assertNotContains(respuesta, 'Cómo interpretar los pesos y datos históricos')

    def test_baja_correccion_y_desactivacion_conservan_consecuencias(self):
        baja = self.client.get(reverse('inventario:crear_baja',args=[self.registro.pk]))
        self.assertContains(baja, 'La baja retira la caja completa de forma definitiva. Se conserva su trazabilidad.')
        self.assertContains(baja, 'role="alert"')
        correccion = self.client.get(reverse('trazabilidad:corregir_recepcion',args=[self.partida.detalle_recepcion.recepcion_id]))
        self.assertContains(correccion, 'Los cambios quedarán registrados en el historial.')
        self.assertContains(correccion, 'Las especies y sus pesos están bloqueados para preservar la trazabilidad.')
        usuario = self.client.get(reverse('usuarios:detalle_usuario',args=[self.usuario.pk]))
        self.assertContains(usuario, 'La cuenta dejará de acceder al sistema. Su historial se conserva.')

    def test_advertencia_postproceso_permanece_completa_y_accesible(self):
        html=render_to_string('trazabilidad/partidas/_pesajes_mermas.html',{
            'partida':self.partida,'peso_postproceso':self.partida.pesajes.first(),
            'advertencia_peso':ADVERTENCIA_PESO_SUPERIOR})
        self.assertIn(ADVERTENCIA_PESO_SUPERIOR,html)
        self.assertIn('role="alert"',html)

    def test_dashboard_franja_enlaza_criterios_al_final(self):
        respuesta=self.client.get(reverse('reportes:dashboard'))
        html=respuesta.content.decode()
        self.assertIn('href="#criterios-colores"',html)
        self.assertGreater(html.index('id="criterios-colores"'),html.index('Accesos rápidos'))
        self.assertContains(respuesta,'Criterios de los colores')
        self.assertContains(respuesta,'Sin alertas de frío.')
        self.assertNotContains(respuesta,'¿Cómo se calculan los colores?')
