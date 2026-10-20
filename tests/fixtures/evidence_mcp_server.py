"""Local wire fixture, never used by the deployed agent or as a Toolbox substitute."""

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer

server = MCPServer("evidence-fixture")


@server.tool()
def burden_for_county(county_fips: str) -> list[dict]:
    return [{"geo_id": county_fips + "000001", "total_pop": 1000}]


@server.tool()
def access_for_county(county_fips: str) -> list[dict]:
    return [{"geo_id": county_fips + "000001", "km_to_safety_net": 3}]


@server.tool()
def exposure_for_county(county_fips: str) -> list[dict]:
    return []


@server.tool()
def county_context(county_fips: str) -> list[dict]:
    return [
        {
            "tract_count": 1,
            "disease_weeks": 0,
            "usable_disease_weeks": 0,
            "shortage_area_count": 0,
        }
    ]


if __name__ == "__main__":
    server.run(transport="stdio")
