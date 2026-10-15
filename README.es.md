# WhyNotUp

[![CI](https://github.com/AlvaroPavon/whynotup/actions/workflows/ci.yml/badge.svg)](https://github.com/AlvaroPavon/whynotup/actions/workflows/ci.yml)
[![Integración Docker](https://github.com/AlvaroPavon/whynotup/actions/workflows/docker-integration.yml/badge.svg)](https://github.com/AlvaroPavon/whynotup/actions/workflows/docker-integration.yml)

**Encuentra qué bloquea tu aplicación con Docker Compose, con evidencias y una corrección sugerida.**

WhyNotUp revisa errores habituales de configuración, señala el archivo y la línea relevante y explica qué cambiar. Puedes revisar los archivos sin Docker o añadir comprobaciones de lectura del entorno local.

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

Salida real de la demo, abreviada después del primer hallazgo:

```text
WhyNotUp 0.1.0 — bundled-demo

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

# Selecciona perfiles o filtra los hallazgos de un servicio
whynotup doctor . --profile development --service api

# Guarda un informe para revisarlo antes de compartirlo
whynotup doctor . --format markdown --output diagnostico.md
whynotup doctor . --format json --output diagnostico.json

# Usa un archivo específico para interpolar variables
whynotup doctor . --env-file .env.development
```

Las comprobaciones estáticas no necesitan Docker. `--runtime` necesita Docker con Compose v2 y acceso al daemon. Los comandos de Docker solo leen el estado de Compose y de los contenedores; la comprobación TCP intenta enlazar temporalmente los puertos configurados del host. No hay arranques, paradas, reinicios, compilaciones ni reparaciones automáticas.

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

Los informes distinguen los errores de configuración confirmados de las causas probables e indican qué comprobaciones se omitieron o no estaban disponibles. Un informe limpio significa que las comprobaciones realizadas no detectaron esos problemas; no garantiza que la aplicación arranque o funcione correctamente.

### Formatos y códigos de salida

El formato predeterminado es texto legible. `--format json` devuelve un informe estructurado con `schema_version: "1.0"`; `--format markdown` produce un informe para compartir. Solo `--output` escribe un archivo de informe. Nunca se sobrescriben archivos existentes.

| Código de salida | Significado |
| --- | --- |
| `0` | Ningún hallazgo alcanza el umbral de fallo elegido |
| `1` | Uno o más hallazgos alcanzan ese umbral |
| `2` | Entrada o uso de la línea de comandos incorrectos |
| `3` | Se solicitó runtime, sus comprobaciones fueron incompletas o no estaban disponibles y ningún hallazgo alcanzó el umbral |

Selecciona el umbral con `--fail-on error`, `--fail-on warning` o `--fail-on none`. Desactivar el fallo por hallazgos no oculta errores de entrada ni la falta de comprobaciones runtime.

## Alcance y límites

Esta primera versión se centra en problemas habituales de arranque con Docker Compose.

- **Un archivo Compose por ejecución.** WhyNotUp no combina archivos. Rechaza configuraciones implícitas de varios archivos y overrides; usa `-f` para revisar explícitamente uno. El resultado se refiere a ese archivo, no al despliegue combinado.
- **Configuración, sin ejecutar la aplicación.** No analiza el código de la aplicación, ejecuta contenedores, recoge logs ni valida credenciales de bases de datos. Las reglas de host y red se basan en el contexto disponible en los archivos.
- **Entorno local.** Los puertos se comprueban en la máquina que ejecuta WhyNotUp. Esto no determina la disponibilidad de puertos en un host Docker remoto ni revisa UDP. Los puertos de los contenedores del propio proyecto ayudan a evitar falsos conflictos.
- **Cobertura explícita.** Se rechazan `include`, `extends` de servicios y etiquetas YAML personalizadas. El informe avisa de comprobaciones omitidas y límites de perfiles. Algunos proyectos requieren revisiones fuera del alcance de la herramienta.
- **Revisa antes de compartir.** Texto, Markdown y JSON usan la misma ocultación de secretos, que es de mejor esfuerzo. Pueden quedar secretos no reconocidos. Lee todo el informe antes de publicarlo.

## GitHub Action

La [Action incluida](docs/GITHUB_ACTION.md) añade el diagnóstico al resumen del job y expone recuentos de hallazgos. El workflow de CI está preparado para comprobarla con `uses: ./`. Para usarla desde otros repositorios primero hay que publicar este repositorio y una referencia de versión.

## Desarrollo y contribuciones

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

Si falta una comprobación útil, abre un issue con un ejemplo mínimo y el comportamiento esperado. Retira antes las credenciales, rutas privadas y nombres de host sensibles. Una buena regla nueva incluye una configuración defectuosa y otra válida que no deba marcarse; consulta [CONTRIBUTING.md](CONTRIBUTING.md).

La [guía de la primera publicación](docs/LAUNCH.md) recoge los pasos para mantenedores. Publicar el repositorio y ejecutar las comprobaciones alojadas en GitHub son pasos separados de la validación local.

Si WhyNotUp te ha ayudado, una estrella facilita encontrarlo de nuevo. Sigue a [Álvaro Pavón](https://github.com/AlvaroPavon) para conocer nuevas comprobaciones y la explicación de los problemas que resuelven.

Licencia MIT. Consulta [LICENSE](LICENSE).
