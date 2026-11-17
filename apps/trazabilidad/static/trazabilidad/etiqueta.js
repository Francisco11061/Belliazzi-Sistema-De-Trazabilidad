(function () {
    var boton = document.getElementById('imprimir');
    var qr = document.getElementById('qr');
    function imprimir() { window.print(); }
    if (boton) boton.addEventListener('click', imprimir);
    // Abre el diálogo de impresión una vez cargado el QR, para que salga completo.
    if (qr) {
        if (qr.complete && qr.naturalWidth) { setTimeout(imprimir, 150); }
        else { qr.addEventListener('load', function () { setTimeout(imprimir, 150); }, { once: true }); }
    }
})();
