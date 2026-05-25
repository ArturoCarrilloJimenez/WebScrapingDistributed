import asyncio
import signal as sys_signal
from shared.logging import Logger
from dependencies import get_worker_controller

log = Logger("Worker Main")

async def main():
    # Obtenemos el controlador configurado desde el proveedor de dependencias
    controller = get_worker_controller()
    background_tasks = set()
    loop = asyncio.get_running_loop()

    log.info("Arrancando Worker en modo funcional...")
    
    # Esta función se ejecutará inmediatamente cuando alguien pulse Ctrl+C o Docker pare el contenedor
    def trigger_shutdown():
        # Creamos la tarea
        task = asyncio.create_task(controller.stop())
        
        # La añadimos al set para mantener una referencia fuerte
        background_tasks.add(task)
        
        # Nos aseguramos de eliminar la referencia cuando la tarea termine
        task.add_done_callback(background_tasks.discard)

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