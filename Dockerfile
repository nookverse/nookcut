# NookCut MCP server over Streamable HTTP, with ffmpeg and a caption font included.
#   docker build -t nookcut .
#   docker run --rm -p 8765:8765 -e NOOKCUT_TOKEN=change-me -v "$PWD/media:/media" nookcut
# Then connect agents to http://localhost:8765/mcp with header  Authorization: Bearer change-me
# Video paths passed to tools are paths inside the container, e.g. /media/raw.mov
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[mcp]"

RUN useradd --create-home nookcut && mkdir -p /media && chown nookcut /media
USER nookcut

ENV NOOKCUT_TRANSPORT=http \
    NOOKCUT_HOST=0.0.0.0 \
    NOOKCUT_PORT=8765 \
    NOOKCUT_ALLOWED_DIRS=/media \
    NOOKCUT_FONT=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf

EXPOSE 8765
CMD ["nookcut-mcp"]
