import asyncio
import os
import subprocess


async def spawn(command: list[str], **kwargs) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *command, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        **kwargs,
    )


async def stop(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    if process.stdin:
        process.stdin.close()
    try:
        await asyncio.wait_for(process.wait(), 5)
    except asyncio.TimeoutError:
        if os.name == "nt":
            killer = await spawn(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                 stdout=asyncio.subprocess.DEVNULL,
                                 stderr=asyncio.subprocess.DEVNULL)
            await killer.wait()
        else:
            process.kill()
        await process.wait()
