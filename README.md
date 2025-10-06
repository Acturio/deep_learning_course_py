# Hub de Especialidad: Deep Learning

<https://acturio.github.io/deep_learning_course_py/>

## 1. Usar Bookdown y Python a través RStudio Server con Docker

Este repositorio contiene una configuración en **Docker** que permite levantar un entorno de **RStudio Server** para trabajar el proyectos de **R Bookdown** y ejecutar también un entorno virtual de **Python** dentro del mismo contenedor. Esto se logra mediante lo siguiente:

-   **Dockerfile** → crea una imagen con RStudio preconfigurado, soporte para **Bookdown** y un **Ambiente virtual de Python**.\
-   **Makefile** → un script que simplifica el uso de Docker con comandos cortos (`make build`, `make start`, etc.).\
-   **Volumen compartido** → sincroniza los archivos del proyecto local con el contenedor (`/home/rstudio/project`).
-   **requirements.txt** un archivo para añadir las dependencias del ambiente virtual.

### 1.1 Flujo de trabajo

Con el archivo Makefile, podemos hacer las siguientes acciones:

#### 🔨 1. Construir la imagen

Crea la imagen Docker llamada **dlhub-rstudio** a partir del `Dockerfile` (puede tardar un poco la primera vez o si especificas ciertos paquetes en el `requirements.txt`).

Un vide tutorial de como usarlo se puede consultar [aquí](https://drive.google.com/file/d/1KFbvFchnqvybFSRn0hoeNwkGu_Lr1zzq/view?usp=share_link).

```{bash, eval = F}
make build
```

#### ▶️ 2. Iniciar el contenedor

```{bash, eval = F}
make start
```

Este comando levanta un contenedor con: - RStudio accesible desde el navegador en `http://localhost:8787` - Usuario: `rstudio` - Contraseña: `dlhub` - Carpeta del proyecto local montada en `/home/rstudio/project` - Al interior, hay un ambiente virtual que tiene las librerias que especifiquemos en el `requirements.txt`

------------------------------------------------------------------------

### 💻 3. Entrar al contenedor (modo bash)

```{bash, eval = F}
make bash
```

Abre una sesión interactiva dentro del contenedor (útil para instalar paquetes o probar comandos).

------------------------------------------------------------------------

### 📚 4. Compilar el libro Bookdown

```{bash, eval = F}
make book
```

Ejecuta `bookdown::render_book()` dentro del contenedor y genera la versión HTML del libro (formato **gitbook**) sin necesidad de entrar manualmente a RStudio.

------------------------------------------------------------------------

### ⏹️ 5. Detener el contenedor

```{bash, eval = F}
make stop
```

Detiene el contenedor en ejecución, pero no lo elimina.

------------------------------------------------------------------------

### 🧹 6. Eliminar el contenedor

```{bash, eval = F}
make clean
```

Elimina completamente el contenedor (por si necesitas reconstruirlo desde cero).

### 1.2 Algunos Consejos

-   Si cambias el contenido del `Dockerfile`, ejecuta `make build` nuevamente para reconstruir la imagen.\
-   Todo el contenido del proyecto se guarda en tu máquina local, no dentro del contenedor.\
-   Puedes abrir RStudio en el navegador (en `http://localhost:8787`), escribir código en R o Python, y compilar directamente el libro.
