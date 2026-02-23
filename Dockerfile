FROM rocker/rstudio:latest

# 1. Install System Dependencies + Python 3.11 PPA
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    libssl-dev \
    libcurl4-openssl-dev \
    libxml2-dev \
    make \
    pandoc \
    texlive \
    && rm -rf /var/lib/apt/lists/*

# 2. Setup R packages
RUN R -e "install.packages(c('bookdown', 'knitr', 'rmarkdown'), lib='/usr/local/lib/R/site-library')"
RUN R -e "install.packages(c('magrittr','stringl','stringr', 'ggplot2'), lib='/usr/local/lib/R/site-library')"
RUN R -e "install.packages('png', lib='/usr/local/lib/R/site-library')"
RUN R -e "install.packages('reticulate', lib='/usr/local/lib/R/site-library')"

# 3. Setup Python 3.11 Virtual Environment
RUN python3.11 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel \
    && /opt/venv/bin/pip install --no-cache-dir \
       torch==2.4.0+cpu \
       torchvision==0.19.0+cpu \
       torchaudio==2.4.0+cpu \
       --index-url https://download.pytorch.org/whl/cpu

# 4. Install PyG Binaries specifically for Torch 2.4.0+cpu
RUN /opt/venv/bin/pip install --no-cache-dir \
    torch-scatter==2.1.2 \
    torch-sparse==0.6.18 \
    torch-cluster==1.6.3 \
    torch-spline-conv==1.2.2 \
    -f https://data.pyg.org/whl/torch-2.4.0+cpu.html

# 4b. Install the main PyG library (this doesn't require a special URL)
RUN /opt/venv/bin/pip install --no-cache-dir torch-geometric

# 5. Install requirements.txt (Now compatible with 3.11)
COPY requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

ENV RETICULATE_PYTHON=/opt/venv/bin/python
WORKDIR /home/rstudio/project

COPY . /home/rstudio/project
COPY data/wikipedia/chameleon /home/rstudio/project/data/wikipedia/chameleon

RUN chown -R rstudio:rstudio /home/rstudio/project

EXPOSE 8787
