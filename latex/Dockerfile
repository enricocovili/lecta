# Lecta compile service: full TeX Live, no network, unprivileged.
FROM texlive/texlive:TL2025-historic

# mutool: dvisvgm reads PDFs through it (the draft is typeset per block and shown as SVG).
RUN apt-get update \
 && apt-get install -y --no-install-recommends mupdf-tools \
 && rm -rf /var/lib/apt/lists/*

# Pre-build the LuaTeX font database into TEXMFSYSVAR so per-job (empty)
# TEXMFVAR dirs don't rebuild it on every lualatex run.
RUN luaotfload-tool --update --force >/dev/null 2>&1 || true \
 && mktexlsr >/dev/null 2>&1 || true

# The base image already ships an unprivileged `texlive` user with uid 1000.
RUN mkdir -p /run/lecta /latex-work /app \
 && chown 1000:1000 /run/lecta /latex-work

COPY compile_server.py latexmkrc healthcheck.py /app/
USER 1000:1000
ENV PYTHONUNBUFFERED=1 \
    LECTA_LATEX_SOCKET=/run/lecta/latex.sock \
    LECTA_LATEX_WORK=/latex-work
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD ["python3", "/app/healthcheck.py"]
CMD ["python3", "/app/compile_server.py"]
