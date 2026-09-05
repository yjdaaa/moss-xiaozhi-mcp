from mcp.server.fastmcp import FastMCP
import os
import importlib
import sys
from dotenv import load_dotenv

load_dotenv()

ENABLED_IP_CAMERA = os.getenv('ENABLED_IP_CAMERA')
DISABLED_TOOLS = {
    'tools.homeasstant_tool',
    'tools.lasergrbl_gui_tool',
}
PRIORITY_TOOLS = [
    'tools.check_laser_connection_tool',
    'tools.laser_safe_action_tool',
    'tools.laser_workflow_tool',
    'tools.laser_material_calibration_tool',
    'tools.text_laser_task_tool',
    'tools.text_image_gcode_tool',
    'tools.laser_grbl_tool',
    'tools.laser_network_grbl_tool',
]

# Create an MCP server
mcp = FastMCP("YOKO_MCP_SERVER")

# 自动导入并注册tools文件夹中的所有模块
tools_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'tools')


def _iter_tool_modules():
    discovered = []
    for filename in os.listdir(tools_dir):
        if filename.endswith('.py') and filename != '__init__.py':
            discovered.append(f'tools.{filename[:-3]}')

    yielded = set()
    for module_name in PRIORITY_TOOLS:
        if module_name in discovered:
            yielded.add(module_name)
            yield module_name
    for module_name in sorted(discovered):
        if module_name not in yielded:
            yield module_name


for module_name in _iter_tool_modules():
        try:
            module = importlib.import_module(module_name)
            if hasattr(module, 'register_tool'):
                if ENABLED_IP_CAMERA != 'true' and module_name == 'tools.camera_tool':
                    continue
                if module_name in DISABLED_TOOLS:
                    continue
                module.register_tool(mcp)
        except Exception as e:
            print(f"Failed to load {module_name}: {e}", file=sys.stderr)

# Start the server
if __name__ == "__main__":
    mcp.run(transport="stdio")
