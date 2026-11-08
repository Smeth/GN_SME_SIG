"""Santé connexion MySQL — messages utilisateur et sonde réseau."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sme_gend.config.settings import Settings


@dataclass(frozen=True, slots=True)
class MySQLProbeResult:
    ok: bool
    message: str
    technical: str = ""


def probe_mysql(settings: Settings) -> MySQLProbeResult:
    from sme_gend.repositories.mysql_connection import MySQLConnection

    ok, msg = MySQLConnection(settings).test_connection()
    if ok:
        s = settings
        return MySQLProbeResult(
            ok=True,
            message=f"MySQL {s.mysql_host}:{s.mysql_port}/{s.mysql_database}",
        )
    return MySQLProbeResult(
        ok=False,
        message=format_mysql_error_for_user(msg),
        technical=msg,
    )


def format_mysql_error_for_user(raw: str | BaseException) -> str:
    """Message lisible pour l'utilisateur (déploiement / perte réseau)."""
    message = _format_mysql_error(raw)
    stage = getattr(raw, 'reset_stage', None)
    if stage:
        message = f'Réinitialisation interrompue — {stage}.\n\n{message}'
    if getattr(raw, 'reset_commit_uncertain', False):
        message += ('\n\nLa validation MySQL n’a pas pu être confirmée. La maintenance et les fichiers de récupération '
                    'sont conservés. Vérifiez le journal avant toute reprise ; ne relancez pas la réinitialisation.')
    if getattr(raw, 'reset_recovery_required', False):
        message += ('\n\nLa récupération des fichiers n’a pas pu être confirmée. La maintenance est conservée ; '
                    'vérifiez la sauvegarde et le dossier de récupération avant toute reprise.')
    backup = getattr(raw, 'reset_safety_backup', None)
    if backup:
        message += '\n\nSauvegarde préalable conservée : '+backup
    return message


def _format_mysql_error(raw):
    text = str(raw or "").strip()
    low = text.casefold()
    if not text:
        return "Connexion MySQL impossible."
    code = raw.args[0] if isinstance(raw, BaseException) and raw.args else None
    if code in (2006, 2013) or 'server has gone away' in low or 'lost connection to mysql' in low:
        if 'timed out' in low or 'timeout' in low:
            return ('La requête MySQL a dépassé le délai de lecture et la connexion a été fermée. '
                    'Vérifiez le résultat de l’opération avant toute nouvelle tentative (code 2013).')
        return ('La connexion MySQL a été interrompue pendant le traitement. '
                'Vérifiez le résultat de l’opération avant toute nouvelle tentative.')
    if code == 0 or text in ("(0, '')", '(0, "")'):
        return ('La connexion MySQL était déjà fermée. Consultez le journal de l’opération '
                'pour identifier l’erreur initiale avant de recommencer.')
    if "1213" in text or "deadlock found" in low or "deadlock detected" in low:
        return (
            "Conflit temporaire entre opérations simultanées sur la base MySQL. "
            "Réessayez l'opération. Si le problème se répète, transmettez le code "
            "1213 à l'administrateur."
        )
    if "1205" in text or "lock wait timeout" in low:
        return (
            "Une autre opération garde un verrou MySQL trop longtemps. "
            "Réessayez après quelques instants; si le problème persiste, "
            "transmettez le code 1205 à l'administrateur."
        )
    if "2003" in text or "can't connect" in low or "connection refused" in low:
        return (
            "Serveur MySQL injoignable (réseau, pare-feu ou service arrêté). "
            "Vérifiez l'adresse IP, le port 3306 et que le serveur est démarré."
        )
    if "2002" in text or "timed out" in low or "timeout" in low:
        return (
            "Délai de connexion dépassé. Vérifiez le réseau, le VPN ou le pare-feu "
            "entre ce poste et le serveur MySQL."
        )
    if "1045" in text or "access denied" in low:
        if "using password: no" in low:
            return (
                "Accès refusé : aucun mot de passe n'a été envoyé à MySQL. "
                "Renseignez le mot de passe puis enregistrez le profil dans paths.json."
            )
        return (
            "Accès refusé (utilisateur ou mot de passe incorrect). "
            "Corrigez les identifiants dans la configuration MySQL."
        )
    if "1049" in text or "unknown database" in low:
        return (
            "Base de données introuvable sur le serveur. "
            "Vérifiez le nom de la base ou importez le schéma SME."
        )
    if "1044" in text or "1042" in text:
        return "Droits insuffisants sur la base MySQL pour cet utilisateur."
    host_hint = re.search(r"host[^']*'([^']+)'", text, re.I)
    if host_hint:
        return f"Connexion à {host_hint.group(1)} impossible.\n{text}"
    return text


def startup_mysql_help_html(*, paths_file: str) -> str:
    return (
        "<p>Indiquez le <b>serveur MySQL</b> (IP ou nom), le port (<b>3306</b>), "
        "la base (<b>smegend</b>), l'utilisateur et le mot de passe.</p>"
        f"<p>Enregistrement : <code>{paths_file}</code></p>"
        "<p>Après démarrage : menu <b>Fichier → Configuration MySQL et chemins…</b></p>"
    )
