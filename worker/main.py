import asyncio
import signal as sys_signal
from shared.logging import Logger
from dependencies import get_worker_controller

log = Logger("Worker Main")

async def main():
    # Obtenemos el controlador configurado desde el proveedor de dependencias
    controller = get_worker_controller()

    log.info("Arrancando Worker en modo funcional...")
    loop = asyncio.get_running_loop()
    
    # Esta función se ejecutará inmediatamente cuando alguien pulse Ctrl+C o Docker pare el contenedor
    def trigger_shutdown():
        asyncio.create_task(controller.stop())

    # Registramos las señales en el bucle de eventos
    for sig in (sys_signal.SIGINT, sys_signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, trigger_shutdown)
        except NotImplementedError:
            pass  # Evita que falle si pruebas en Windows local

    try:
        # Foco total en el bucle de ejecución
        await controller.run()
    except Exception as e:
        log.error(f"Error fatal: {e}")

if __name__ == "__main__":
    asyncio.run(main())