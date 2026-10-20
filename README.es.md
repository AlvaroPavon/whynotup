# WhyNotUp

[![CI](https://github.com/AlvaroPavon/whynotup/actions/workflows/ci.yml/badge.svg)](https://github.com/AlvaroPavon/whynotup/actions/workflows/ci.yml)
[![Integración Docker](https://github.com/AlvaroPavon/whynotup/actions/workflows/docker-integration.yml/badge.svg)](https://github.com/AlvaroPavon/whynotup/actions/workflows/docker-integration.yml)

**Encuentra qué bloquea tu aplicación con Docker Compose, con evidencias y una corrección sugerida.**

WhyNotUp revisa errores habituales de configuración, señala el archivo y la línea relevante y explica qué cambiar. Empieza sin Docker, solicita una revisión determinista de configuración o activa validación nativa, comprobaciones runtime y análisis acotado de patrones en logs.

Funciona en tu máquina sin IA, claves de API, telemetría, cuentas ni cuotas de uso. No envía datos del proyecto a un servicio. Tu máquina, entorno Docker, runner de CI o proveedor de alojamiento pueden tener sus costes habituales.

Creado por [Álvaro Pavón](https://github.com/AlvaroPavon). [English](README.md) · [Cómo contribuir](CONTRIBUTING.md) · [Seguridad](SECURITY.md)

## Prueba la demo

Necesitas Git y Python 3.10 o posterior:

```bash
git clone https://github.com/AlvaroPavon/whynotup.git
cd whynotup
python -m venv .venv
source .venv/bin/activate
python -m pip install .
whynotup demo
```

En PowerShell de Windows, activa el entorno con `.\.venv\Scripts\Activate.ps1`. La demo incluida funciona sin Docker y devuelve el código `1` intencionadamente porque contiene problemas. Esta versión se instala desde el código fuente; todavía no hay una publicación en PyPI.

Ejemplo de salida de la demo, abreviada después del primer hallazgo:

```text
WhyNotUp 0.2.0 — bundled-demo

Compose file: compose.yaml

4 finding(s): 2 error(s), 2 warning(s).

[ERROR] WNU001 (api): Required variable APP_SECRET is not set
Confidence: confirmed
Compose cannot resolve a required interpolation value.
- Evidence [compose.yaml:5]: Interpolation references APP_SECRET.
Suggestion: Set APP_SECRET in the interpolation environment or the project dotenv file. No value is guessed.
```

Compara los [ejemplos de configuración defectuosa y corregida](examples/README.md) sin arrancar contenedores:

```bash
whynotup doctor examples/broken -f compose.yaml  # 3 hallazgos; salida 1
whynotup doctor examples/fixed -f compose.yaml   # Sin hallazgos; salida 0
```

Estos recuentos presuponen los valores dotenv de los ejemplos; las variables del shell tienen prioridad. Los ejemplos sirven para practicar diagnósticos; su contenedor de API solo espera. Un informe estático limpio no demuestra que una aplicación arranque correctamente.

## Un problema reconocible

La base de datos se llama `db` en Compose, pero el contenedor de la API está configurado así:

```yaml
services:
  api:
    image: example/api
    environment:
      DB_HOST: localhost
    depends_on:
      - db
  db:
    image: postgres:16
```

Dentro de un contenedor aislado, `localhost` se refiere al propio contenedor. WhyNotUp puede identificar el servicio de base de datos y sugerir su nombre:

```diff
- DB_HOST: localhost
+ DB_HOST: db
```

El informe incluye la evidencia y el grado de confianza. Tú revisas la sugerencia: WhyNotUp no la aplica. Esta comprobación identifica una configuración probablemente incorrecta, pero no demuestra por sí sola la causa del fallo de la aplicación.

## Revisa tu proyecto

```bash
# Revisa la configuración del directorio actual
whynotup doctor .

# Selecciona explícitamente un único archivo Compose
whynotup doctor ./mi-app -f compose.yaml

# Añade estado de contenedores y comprobación de puertos TCP locales
whynotup doctor ./mi-app -f compose.yaml --runtime

# Valida el archivo con Compose v2 instalado, sin necesitar el daemon
whynotup doctor ./mi-app -f compose.yaml --validate

# Revisa decisiones de configuración para producción, sin Docker
whynotup review ./mi-app -f compose.yaml --target production
whynotup doctor ./mi-app -f compose.yaml --recommendations --target development

# Analiza logs recientes; también activa las comprobaciones runtime
whynotup doctor ./mi-app -f compose.yaml --logs --log-tail 100 --service api

# Solicita validación, runtime, logs y recomendaciones juntos
whynotup doctor ./mi-app -f compose.yaml --all --target production

# Selecciona perfiles o filtra los hallazgos de un servicio
whynotup doctor . --profile development --service api

# Guarda un informe para revisarlo antes de compartirlo
whynotup doctor . --format markdown --output diagnostico.md
whynotup doctor . --format json --output diagnostico.json

# Usa un archivo específico para interpolar variables
whynotup doctor . --env-file .env.development

# Las advertencias pueden hacer fallar CI; none no oculta checks no disponibles
whynotup review . --target production --fail-on warning
whynotup doctor . --all --fail-on none
```

Las comprobaciones estáticas y `review` no necesitan Docker. `review` equivale a `doctor` con recomendaciones activadas; `--target production|development` elige la política de revisión, no los perfiles Compose. `doctor --recommendations` usa la misma política. El target predeterminado es `production`.

`--validate` ejecuta `docker compose ... config --quiet` con el archivo, entorno y perfiles seleccionados. Requiere Docker CLI y Compose v2, pero no un daemon. Primero se ejecuta el cargador de WhyNotUp, por lo que una entrada no compatible puede rechazarse antes de la validación nativa. Que Compose acepte el archivo no demuestra el arranque de la aplicación.

Si está definido `COMPOSE_ENV_FILES`, selecciona un único archivo de interpolación con `--env-file`. `COMPOSE_DISABLE_ENV_FILE` permite desactivar el `.env` predeterminado; un `--env-file` explícito sigue teniendo prioridad. Los comandos nativos conservan la selección del cargador.

`--runtime` necesita Compose v2 y acceso al daemon. `--logs` implica runtime; `--all` solicita validación, runtime, logs y recomendaciones, por lo que también necesita el daemon. Las operaciones leen el estado y, solo cuando se solicita, logs recientes acotados; la comprobación TCP intenta enlazar temporalmente los puertos del host. No hay arranques, paradas, reinicios, compilaciones ni reparaciones automáticas.

### Comprobaciones

| Código | Qué comprueba | Modo |
| --- | --- | --- |
| `WNU001` | Interpolación de variables ausentes o sin resolver | Estático |
| `WNU002` | Archivo de entorno requerido que no existe | Estático |
| `WNU003` | Ruta del host ausente en un montaje bind | Estático |
| `WNU004` | Puerto TCP publicado del host aparentemente ocupado | Runtime |
| `WNU005` | Host loopback usado para un servicio vecino identificable | Estático |
| `WNU006` | Puerto publicado del vecino usado en lugar de su puerto interno | Estático |
| `WNU007` | Dependencia requerida sin ejecutar o con estado unhealthy | Runtime |
| `WNU008` | Dependencia requerida ausente o excluida por perfiles | Estático |
| `WNU009` | Contenedor terminado o reiniciándose | Runtime |
| `WNU010` | Compose instalado rechaza la validación `config --quiet` | `--validate` |

### Revisión opcional de configuración

Todas las recomendaciones son advertencias. Señalan decisiones que conviene revisar; no demuestran vulnerabilidades, acceso desde Internet, readiness ni consumo real de recursos. `--fail-on warning` también incluye estas advertencias en el umbral de fallo.

| Código | Recomendación | Excepciones y alcance |
| --- | --- | --- |
| `WNU101` | Fija una imagen con tag implícita o `latest` | Un digest ya fija la imagen |
| `WNU102` | Revisa `privileged: true` | Algunas integraciones con el host lo necesitan intencionadamente |
| `WNU103` | Revisa un usuario root o UID `0` explícito | Un usuario omitido no se trata como prueba de root |
| `WNU104` | Revisa puertos publicados en todas las interfaces | No demuestra acceso externo; se ignoran mappings con red host |
| `WNU105` | Revisa un montaje bind de `docker.sock`, incluso de solo lectura | Un volumen con nombre no se identifica como socket del host |
| `WNU106` | Retira literales sensibles o valores predeterminados literales de interpolación en `environment` | Se excluyen referencias puras, pass-through y `env_file`; no se muestran valores |
| `WNU107` | Revisa dependencias que esperan inicio, sin esperar readiness | Se excluyen dependencias opcionales, trabajos completados y servicios ausentes; no se inventa un healthcheck |
| `WNU108` | Declara límites de CPU y memoria | Solo target production; recomendación, no medición |
| `WNU109` | Revisa la ausencia de una política de reinicio | Solo target production; un trabajo puntual puede omitirla intencionadamente |
| `WNU110` | Revisa el uso de red host | Algunas integraciones la necesitan intencionadamente |
| `WNU111` | Declara almacenamiento persistente para una imagen de base de datos reconocida | Solo target production; no demuestra qué persistencia existe fuera de la configuración conocida |

### Análisis opcional de logs

No se leen logs salvo que pases `--logs` o `--all`. `--service` limita los contenedores seleccionados **antes** de leer logs. `--log-tail` acepta `1..1000`, vale `100` por defecto y acota las líneas recientes solicitadas por contenedor; además, la recogida se limita a 16 contenedores y 64 KiB por contenedor. Una lectura fallida, agotada o truncada marca el check como no disponible; el informe explica los límites.

Los hallazgos son advertencias con confianza `possible`: incluyen la familia del patrón y el número de líneas coincidentes, sin líneas originales. Los logs sin procesar no se guardan ni se incluyen en texto, Markdown o JSON. Un patrón puede corresponder a un fallo antiguo, ya recuperado o de una dependencia; no demuestra la causa actual. Pueden perderse errores anteriores o no reconocidos.

| Código | Patrón reconocible en logs |
| --- | --- |
| `WNU201` | Conexión rechazada |
| `WNU202` | Error de resolución DNS o de nombres |
| `WNU203` | Autenticación denegada o fallida |
| `WNU204` | Permiso denegado |
| `WNU205` | Memoria agotada |
| `WNU206` | Dirección o puerto ya en uso |

Los informes distinguen los errores de configuración confirmados de las causas probables e indican qué comprobaciones se omitieron o no estaban disponibles. Un informe limpio significa que las comprobaciones realizadas no detectaron esos problemas; no garantiza que la aplicación arranque o funcione correctamente.

### Formatos y códigos de salida

El formato predeterminado es texto legible. `--format json` devuelve un informe estructurado con `schema_version: "1.0"`; `--format markdown` produce un informe para compartir. Solo `--output` escribe un archivo de informe. Nunca se sobrescriben archivos existentes.

| Código de salida | Significado |
| --- | --- |
| `0` | Ningún hallazgo alcanza el umbral de fallo elegido |
| `1` | Uno o más hallazgos alcanzan ese umbral |
| `2` | Entrada o uso de la línea de comandos incorrectos |
| `3` | Una comprobación solicitada de validación, runtime o logs no estaba disponible y ningún hallazgo alcanzó el umbral |

Selecciona el umbral con `--fail-on error`, `--fail-on warning` o `--fail-on none`. Desactivar el fallo por hallazgos no oculta errores de entrada ni checks no disponibles. El informe también explica la cobertura omitida, incompleta o limitada; revísalo aunque la salida sea `0`.

## Alcance y límites

La versión 0.2 añade revisión opcional, validación nativa e indicios de logs al diagnóstico de problemas habituales de arranque con Docker Compose.

- **Un archivo Compose por ejecución.** WhyNotUp no combina archivos. Rechaza configuraciones implícitas de varios archivos y overrides; usa `-f` para revisar explícitamente uno. El resultado se refiere a ese archivo, no al despliegue combinado.
- **Configuración y observaciones acotadas.** No analiza el código de la aplicación, ejecuta contenedores ni valida credenciales de bases de datos. La validación nativa y la lectura de logs son opcionales. Las reglas de host y red usan el contexto disponible en los archivos.
- **Entorno local.** Los puertos se comprueban en la máquina que ejecuta WhyNotUp. Esto no determina la disponibilidad de puertos en un host Docker remoto ni revisa UDP. Los puertos de los contenedores del propio proyecto ayudan a evitar falsos conflictos.
- **Cobertura explícita.** Se rechazan `include`, `extends` de servicios y etiquetas YAML personalizadas. El informe avisa de comprobaciones omitidas y límites de perfiles. Algunos proyectos requieren revisiones fuera del alcance de la herramienta.
- **Revisa antes de compartir.** Texto, Markdown y JSON usan la misma ocultación de secretos, que es de mejor esfuerzo. Pueden quedar secretos no reconocidos. Lee todo el informe antes de publicarlo.

## GitHub Action

La [Action incluida](docs/GITHUB_ACTION.md) añade el diagnóstico al resumen del job y expone recuentos de hallazgos. Admite validación, runtime, logs y recomendaciones opcionales. CI la comprueba con `uses: ./`; los ejemplos remotos usan `@main`, que conviene sustituir por un SHA completo revisado para reproducir la ejecución. No se presupone que exista una etiqueta `v0.2.0`.

## Desarrollo y contribuciones

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

Si falta una comprobación útil, abre un issue con un ejemplo mínimo y el comportamiento esperado. Retira antes las credenciales, rutas privadas y nombres de host sensibles. Una buena regla nueva incluye una configuración defectuosa y otra válida que no deba marcarse; consulta [CONTRIBUTING.md](CONTRIBUTING.md).

La [guía de publicación](docs/LAUNCH.md) recoge los pasos para mantenedores. Publicar el repositorio y ejecutar las comprobaciones alojadas en GitHub son pasos separados de la validación local.

Si WhyNotUp te ha ayudado, una estrella facilita encontrarlo de nuevo. Sigue a [Álvaro Pavón](https://github.com/AlvaroPavon) para conocer nuevas comprobaciones y la explicación de los problemas que resuelven.

La versión 0.2 usa las condiciones de [LICENSE](LICENSE), SPDX `zlib-acknowledgement`; consulta la [guía de licencia](docs/LICENSING.md). La documentación del producto debe incluir: **Portions Copyright (c) 2026 Álvaro Pavón (AlvaroPavon)**. Conserva el aviso, no falsees la autoría original y marca las versiones de fuentes modificadas. Los forks pueden usar otro nombre. Las copias anteriores distribuidas bajo MIT conservan esa licencia.
