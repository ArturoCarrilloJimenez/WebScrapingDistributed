import asyncio
from shared.logging import Logger
from dependencies import get_worker_controller

log = Logger("Worker Main")

async def main():
    # Obtenemos el controlador configurado desde el proveedor de dependencias
    controller = get_worker_controller()

    log.info("Arrancando Worker en modo funcional...")
    
    try:
        # Foco total en el bucle de ejecución
        await controller.run()
    except Exception as e:
        log.error(f"Error fatal: {e}")

if __name__ == "__main__":
    asyncio.run(main())