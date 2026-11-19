"""Tachado de secretos antes de escribir en el vault.

Un vault OKF es un repo git que se publica: una llave pegada en el cuerpo de una
nota queda en el historial para siempre. `new` y `edit` pasan por acá todo texto
que entra (título, descripción, cuerpo, tags, campos) y guardan la nota con el
valor reemplazado por «[secreto tachado]». La escritura no se rechaza: se guarda
lo que la nota dice y se avisa de lo que se quitó, para que quien la escribió
sepa que esa llave ya viajó y hay que rotarla.

Qué reconoce:
  - llaves con formato propio: bloque PRIVATE KEY, Anthropic, OpenAI, GitHub
    (clásico y fino), GitLab, Slack, AWS, Google, Stripe, Hugging Face,
    Replicate, npm, SendGrid, bot de Telegram, JWT;
  - el valor de una asignación cuyo nombre lo delata (`DB_PASSWORD=…`,
    `api_key: …`, `contraseña = …`), de una cabecera `Authorization` y de las
    credenciales de una URL (`postgres://usuario:CLAVE@host`);
  - una contraseña dicha en prosa («la contraseña del panel es Tucan-Verde-2026»).

Qué NO reconoce, a propósito: un hash de commit, un UUID, la palabra
«contraseña» sin valor, y cualquier secreto sin forma (una frase larga en
minúsculas). Las dos últimas reglas son heurísticas: exigen que el valor tenga
pinta de secreto (un dígito, o mayúsculas y minúsculas mezcladas) para no tachar
«password: obligatoria».

Las reglas se aplican todas sobre el texto ORIGINAL y los tramos que se pisan
se funden en uno solo: aplicarlas en cadena deja restos del marcador cuando dos
reglas ven la misma cadena (`API_KEY=[tachado] tachado]`).

Se apaga por vault con `writes.redact: false` en `.okf.config.yaml`.
"""

import re

MARCA = "[secreto tachado]"

_NOMBRE_SECRETO = (
    r"api[_-]?key|secret[_-]?key|client[_-]?secret|access[_-]?token|auth[_-]?token|"
    r"refresh[_-]?token|password|passwd|pwd|contrase[nñ]a"
)


def _parece_secreto(valor: str, exigir_digito: bool = False) -> bool:
    """¿El valor tiene pinta de secreto y no de palabra? Un dígito con letras, o
    (si no se exige dígito) mayúsculas y minúsculas mezcladas. Un marcador de
    posición (`<tu_llave>`, `${LLAVE}`, `xxxxxxxx`) no cuenta."""
    if valor.startswith(("<", "${", "$", "{{")) or len(set(valor.lower())) <= 2:
        return False
    letras = any(c.isalpha() for c in valor)
    digito = any(c.isdigit() for c in valor)
    if letras and digito:
        return True
    if exigir_digito:
        return False
    return any(c.islower() for c in valor) and any(c.isupper() for c in valor)


# (nombre, patrón, grupo que se tacha, filtro opcional sobre el valor)
_REGLAS = [
    ("llave-privada",
     re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.S), 0, None),
    ("anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"), 0, None),
    ("openai", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}"), 0, None),
    ("github", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"), 0, None),
    ("github-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}"), 0, None),
    ("gitlab", re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}"), 0, None),
    ("slack", re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"), 0, None),
    ("aws", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), 0, None),
    ("google", re.compile(r"\bAIza[A-Za-z0-9_\-]{35}"), 0, None),
    ("stripe", re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}"), 0, None),
    ("huggingface", re.compile(r"\bhf_[A-Za-z0-9]{30,}"), 0, None),
    ("replicate", re.compile(r"\br8_[A-Za-z0-9]{30,}"), 0, None),
    ("npm", re.compile(r"\bnpm_[A-Za-z0-9]{36}"), 0, None),
    ("sendgrid", re.compile(r"\bSG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}"), 0, None),
    ("telegram", re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_\-]{33}"), 0, None),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), 0, None),
    ("credenciales-en-url", re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:([^\s@/]+)@"), 1, None),
    ("authorization",
     re.compile(r"(?i)\bauthorization\s*[:=]\s*(?:bearer|basic|token)\s+([A-Za-z0-9._~+/=\-]{8,})"), 1, None),
    ("asignacion",
     re.compile(rf"(?i)\b[\w.\-]*(?:{_NOMBRE_SECRETO})[\w.\-]*[\"']?\s*[:=]\s*[\"'`]?([^\s\"'`,;]{{8,}})"),
     1, _parece_secreto),
    ("contrasena-en-prosa",
     re.compile(r"(?i)\b(?:contrase[nñ]a|password|clave)\b[^.\n:=]{0,40}?\s(?:es|era|ser[aá]|queda|qued[oó])\s+"
                r"[\"'«`]?([^\s\"'»`]{8,})"),
     1, lambda v: _parece_secreto(v.rstrip(".,;:)»"), exigir_digito=True)),
]


def tachar(texto):
    """Devuelve (texto sin secretos, [nombre de la regla de cada tramo tachado]).

    Un texto sin secretos vuelve idéntico y con la lista vacía. Lo que no es
    `str` vuelve tal cual.
    """
    if not isinstance(texto, str) or not texto:
        return texto, []
    tramos = []
    for nombre, patron, grupo, filtro in _REGLAS:
        for m in patron.finditer(texto):
            valor = m.group(grupo)
            if filtro is not None and not filtro(valor):
                continue
            ini, fin = m.span(grupo)
            if grupo and nombre == "contrasena-en-prosa":
                fin -= len(valor) - len(valor.rstrip(".,;:)»"))     # el punto final no es parte de la clave
            if texto[ini:fin] != MARCA:
                tramos.append((ini, fin, nombre))
    if not tramos:
        return texto, []
    tramos.sort()
    fundidos = [list(tramos[0])]
    for ini, fin, nombre in tramos[1:]:
        if ini <= fundidos[-1][1]:                      # se pisa con el anterior: un solo tachón
            fundidos[-1][1] = max(fundidos[-1][1], fin)
        else:
            fundidos.append([ini, fin, nombre])
    partes, cursor = [], 0
    for ini, fin, _ in fundidos:
        partes.append(texto[cursor:ini])
        partes.append(MARCA)
        cursor = fin
    partes.append(texto[cursor:])
    return "".join(partes), [nombre for _, _, nombre in fundidos]


def tachar_args(args, nombres=("title", "description", "tags", "resource", "body")):
    """Tacha en el sitio los atributos de texto de un Namespace de `new`/`edit`
    y cada `--field clave=valor`. Devuelve la lista de reglas que saltaron."""
    reglas = []
    for nombre in nombres:
        valor = getattr(args, nombre, None)
        if isinstance(valor, str) and valor:
            limpio, r = tachar(valor)
            if r:
                setattr(args, nombre, limpio)
                reglas += r
    specs = getattr(args, "fields", None)
    if specs:
        limpios = []
        for spec in specs:
            limpio, r = tachar(str(spec))
            limpios.append(limpio)
            reglas += r
        if reglas:
            args.fields = limpios
    return reglas


def aviso(reglas):
    """La línea que `new`/`edit` imprimen cuando tacharon algo."""
    if not reglas:
        return ""
    n = len(reglas)
    cuales = ", ".join(sorted(set(reglas)))
    return (f"🔒 {n} secreto{'s' if n != 1 else ''} tachado{'s' if n != 1 else ''} antes de guardar ({cuales}). "
            f"La nota quedó con «{MARCA}» en su lugar; si la llave era real, rotala: ya viajó por esta llamada.")


def activo(config) -> bool:
    """`writes.redact` del vault; encendido si no hay config o no lo dice."""
    return bool(getattr(config, "writes_redact", True)) if config is not None else True
