# Self-hosted transposer: OMR + transposition + PDF engraving in one image.
#
# Stage 1 builds Audiveris from source; stage 2 is the runtime, which needs only
# a JRE, Cairo (for CairoSVG) and the Python package. Build with:
#
#     docker build -t transposer .
#     docker run --rm -p 8000:8000 transposer
#
# Audiveris' JDK requirement moves with its development branch; override with
# --build-arg AUDIVERIS_REF=5.7.1 and a matching JDK if the default drifts.

ARG JDK_VERSION=25
ARG AUDIVERIS_REF=development

# --------------------------------------------------------------------------
# Stage 1: build Audiveris
# --------------------------------------------------------------------------
FROM eclipse-temurin:${JDK_VERSION}-jdk AS audiveris-build
ARG AUDIVERIS_REF

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN git clone --depth 1 --branch "${AUDIVERIS_REF}" \
      https://github.com/Audiveris/audiveris.git audiveris

WORKDIR /build/audiveris
RUN ./gradlew --no-daemon installDist \
 && mv app/build/install/* /opt/audiveris

# --------------------------------------------------------------------------
# Stage 2: runtime
# --------------------------------------------------------------------------
FROM python:3.12-slim AS runtime
ARG JDK_VERSION

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TRANSPOSER_AUDIVERIS=/opt/audiveris/bin/Audiveris \
    TESSDATA_PREFIX=/opt/tessdata \
    TRANSPOSER_MOZART_DIR=/opt/mozart \
    TRANSPOSER_DATA_DIR=/data

# libcairo2 and the font stack are what CairoSVG needs to turn Verovio's SVG
# into PDF; fonts-dejavu keeps lyrics and chord names from rendering as boxes.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      git \
      libcairo2 \
      libgl1 \
      libglib2.0-0 \
      fonts-dejavu-core \
      openjdk-${JDK_VERSION}-jre-headless \
 && rm -rf /var/lib/apt/lists/*

COPY --from=audiveris-build /opt/audiveris /opt/audiveris

# Tesseract language data that includes the legacy engine Audiveris initialises.
RUN mkdir -p /opt/tessdata \
 && curl -fsSLo /opt/tessdata/eng.traineddata \
      https://raw.githubusercontent.com/tesseract-ocr/tessdata/main/eng.traineddata

# The Mozart engine, for clean printed single-staff music.
RUN git clone --depth 1 https://github.com/aashrafh/Mozart.git /opt/mozart

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir '.[web,mozart]'

RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"

CMD ["transposer", "serve", "--host", "0.0.0.0", "--port", "8000"]
