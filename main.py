from datetime import datetime
import json

from fastapi import FastAPI
from fastmcp import FastMCP
from fulcra_api.core import FulcraAPI
import structlog
import uvicorn

logger = structlog.getLogger(__name__)
fulcra = FulcraAPI()
fulcra.authorize()

mcp = FastMCP(name="Fulcra Context Agent",
            instructions="""
            This server provides personal data retrieval tools.
            Always specify the time zone when using times as parameters.
            """
)


@mcp.tool()
async def get_workouts(start_time: datetime, end_time: datetime
) -> str:
    """Get details about the workouts that the user has done during a period of time.

    Args:
        start_time: The starting time of the period in question.
        end_time: the ending time of the period in question.
    """
    workouts = fulcra.apple_workouts(start_time, end_time)
    return f"Workouts during {start_time} and {end_time}: " + json.dumps(workouts)


mcp_asgi_app = mcp.http_app(path="/mcp")

app = FastAPI(lifespan=mcp_asgi_app.lifespan)
app.mount("/", mcp_asgi_app)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=4449)
