FROM rocker/rstudio:latest

# Installing dependecies ..
RUN apt-get update && apt-get install -y \
    python3 python3-pip python3-venv \
    libssl-dev libcurl4-openssl-dev libxml2-dev \
    make pandoc texlive \ 
    #texlive-latex-extra
    #texlive-fonts-recommended texlive-fonts-extra l
    && rm -rf /var/lib/apt/lists/*

# Setup R packages
RUN R -e "install.packages(c('bookdown', 'knitr', 'rmarkdown'), lib='/usr/local/lib/R/site-library')"
RUN R -e "install.packages(c('magrittr','stringl','stringr'), lib='/usr/local/lib/R/site-library')"
RUN R -e "install.packages('reticulate', lib='/usr/local/lib/R/site-library')"

# Setup Python3 with reticulate library
# Create a virtualenv
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip

# Copying the requirements.txt file
COPY requirements.txt /tmp/requirements.txt
RUN if [ -f /tmp/requirements.txt ]; then /opt/venv/bin/pip install -r /tmp/requirements.txt; fi

# Configure reticulate to use the venv
ENV RETICULATE_PYTHON=/opt/venv/bin/python

# Create the project directory inside the container
WORKDIR /home/rstudio/project

# copy the content to the project directory
COPY . /home/studio/project

# adding some permisions
RUN chown -R rstudio:rstudio /home/rstudio/project

# Expose the dedicated RStudio port
EXPOSE 8787
