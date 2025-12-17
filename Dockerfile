FROM rocker/rstudio:latest

# ---------------------------
# 1. System dependencies
# ---------------------------
RUN apt-get update && apt-get install -y \
    python3 python3-pip python3-venv \
    libssl-dev libcurl4-openssl-dev libxml2-dev \
    make pandoc texlive \ 
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------
# 2. Install R packages
# ---------------------------
RUN R -e "install.packages(c('bookdown', 'knitr', 'rmarkdown'), lib='/usr/local/lib/R/site-library')"
RUN R -e "install.packages(c('magrittr','stringl', 'tidyverse'), lib='/usr/local/lib/R/site-library')"
RUN R -e "install.packages('reticulate', lib='/usr/local/lib/R/site-library')"

# ---------------------------
# 3. Create Python virtual environment
# ---------------------------
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip

# ---------------------------
# 4. Install Python packages from the requirements.txt file
# ---------------------------
COPY requirements.txt /tmp/requirements.txt
RUN if [ -f /tmp/requirements.txt ]; then \
        /opt/venv/bin/pip install -r /tmp/requirements.txt ; \
    else \
        /opt/venv/bin/pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu && \
        /opt/venv/bin/pip install numpy matplotlib pandas jupyter ; \
    fi

# ---------------------------
# 5. Configure reticulate
# ---------------------------
ENV RETICULATE_PYTHON=/opt/venv/bin/python

# ---------------------------
# 6. Workspace
# ---------------------------
WORKDIR /home/rstudio/project

# copy the content to the project directory
COPY . /home/rstudio/project

# adding some permisions
RUN chown -R rstudio:rstudio /home/rstudio/project

# Expose the dedicated RStudio port
EXPOSE 8787
