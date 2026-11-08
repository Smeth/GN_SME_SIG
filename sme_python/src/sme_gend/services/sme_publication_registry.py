"""Shared immutable SME documents, numbered revisions and atomic approvals."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from contextlib import contextmanager

from sme_gend.repositories.mysql_connection import MySQLConnection, mysql_import_transaction, mysql_read_snapshot
from sme_gend.services.schema_migrations import require_capability, PUBLICATION_TABLES
from sme_gend.services.sme_export_revision import (
    ARTIFACTS, FORMAT, ExportRevision, verify_export_revision, decode_export_dataset, _encode,
)

FILES = (*ARTIFACTS, "manifest.json")
CHUNK_SIZE = 1024 * 1024
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
STATUSES = ("brouillon", "valide", "remplace")
COLUMNS = ("id,revision_id,mois,revision_no,status,version,created_at,created_by,"
           "validated_at,validated_by,validation_note,replaced_by_id,dataset_footprint,manifest")


class PublicationConflict(RuntimeError):
    pass


class PublicationDataChanged(PublicationConflict):
    pass


@dataclass(frozen=True)
class Publication:
    id: int
    revision_id: str
    mois: str
    revision_no: int
    status: str
    version: int
    created_at: datetime
    created_by: str
    validated_at: datetime | None
    validated_by: str | None
    validation_note: str | None
    replaced_by_id: int | None
    dataset_footprint: str
    manifest: dict
    operation_warning: str = ""

    @property
    def publication_id(self):
        return self.id


def registry_tables():
    return tuple(PUBLICATION_TABLES)


def _allowed(permission, *, admin=False, export=False):
    from sme_gend.services.auth_guard import session_has_permission, session_has_admin_role
    if (not session_has_permission(permission) or (admin and not session_has_admin_role())
            or (export and not session_has_permission("effectifs.export"))):
        raise PermissionError("Votre rôle ne permet pas cette action sur les publications SME.")


def _json(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def _publication(row):
    return Publication(*row[:-1], manifest=_json(row[-1]))


def _get(connection, publication_id, *, lock=False):
    if not isinstance(publication_id, int) or publication_id <= 0:
        raise ValueError("Identifiant de publication invalide.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT " + COLUMNS + " FROM sme_publication WHERE id=%s" + (" FOR UPDATE" if lock else ""), (publication_id,))
        row = cursor.fetchone()
    if row is None:
        raise ValueError("Publication SME introuvable.")
    return _publication(row)


def _month(month):
    from sme_gend.services.mysql_import_period import normalize_import_mois
    return normalize_import_mois(month)


def _canonical(encoded):
    if isinstance(encoded, list):
        return [_canonical(item) for item in encoded]
    if isinstance(encoded, dict):
        result = {key: _canonical(value) for key, value in encoded.items()}
        if result.get("type") == "mapping":
            result["items"].sort(key=lambda pair: json.dumps(pair[0], ensure_ascii=False, sort_keys=True))
        return result
    return encoded


def dataset_footprint(dataset: dict, template_sha256: str) -> str:
    """Logical monthly inputs, excluding capture time, host and UI cache order."""
    normalized = dict(dataset)
    for name in ("rows", "grades", "detaches", "movements"):
        if name in normalized:
            normalized[name] = sorted(normalized[name], key=lambda item:
                json.dumps(_canonical(_encode(item)), ensure_ascii=False, sort_keys=True))
    payload = {"model_sha256": template_sha256, "dataset": _canonical(_encode(normalized))}
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def _audit(settings, action, publication, **details):
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.services.auth_context import resolve_audit_actor
    AppConfigRepository(settings).append_audit(action=action, module="sme_publication",
        actor=resolve_audit_actor(), sme_mois_context=publication.mois,
        payload={"publication_id": publication.id, "revision_id": publication.revision_id,
                 "revision_no": publication.revision_no, "status": publication.status,
                 "dataset_footprint": publication.dataset_footprint, **details})


@contextmanager
def _confirmed_result(result):
    """A mirror error after commit must not encourage a duplicate approval."""
    from sme_gend.repositories.mysql_connection import CommittedMirrorError
    try:
        yield
    except CommittedMirrorError:
        if "publication" not in result:
            raise
        result["publication"] = replace(result["publication"], operation_warning=
            "Opération confirmée dans MySQL ; une copie locale du journal reste à vérifier. Ne recommencez pas l'opération.")


def _lock_period(connection, month):
    with connection.cursor() as cursor:
        cursor.execute("INSERT IGNORE INTO sme_publication_period(mois) VALUES(%s)", (month,))
        cursor.execute("SELECT next_revision,current_validated_id,version FROM sme_publication_period WHERE mois=%s FOR UPDATE", (month,))
        state = cursor.fetchone()
        cursor.execute("SELECT COALESCE(MAX(revision_no),0) FROM sme_publication WHERE mois=%s", (month,))
        maximum = int(cursor.fetchone()[0])
        if state is None or int(state[0]) != maximum + 1:
            raise PublicationConflict("Le compteur documentaire est incohérent ; vérifiez le registre avant de continuer.")
        return state


def _verified_artifacts(connection, publication, *, content=()):
    from sme_gend.services.operation_progress import operation_progress
    with operation_progress((('metadata', 'Contrôle du manifeste et des composants'),
                             ('files', 'Lecture des fragments et vérification des empreintes'))) as operation:
        return _verified_artifacts_with_progress(connection, publication, content=content, operation=operation)


def _verified_artifacts_with_progress(connection, publication, *, content, operation):
    operation.start('metadata')
    if (publication.status not in STATUSES or publication.version < 1 or publication.revision_no < 1
            or not isinstance(publication.manifest, dict)
            or publication.manifest.get("format") != FORMAT
            or publication.manifest.get("mois") != publication.mois
            or publication.manifest.get("revision_id") != publication.revision_id):
        raise ValueError("Métadonnées de publication incohérentes.")
    declared_artifacts = publication.manifest.get("artifacts")
    if (not isinstance(declared_artifacts, dict) or set(declared_artifacts) != set(ARTIFACTS)
            or any(not isinstance(value, dict) for value in declared_artifacts.values())):
        raise ValueError("Manifeste de composants de publication invalide.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT artifact_name,byte_size,sha256,chunk_count FROM sme_publication_artifact "
                       "WHERE publication_id=%s ORDER BY artifact_name", (publication.id,))
        metadata = cursor.fetchall()
    if {row[0] for row in metadata} != set(FILES):
        raise ValueError("Publication incomplète : composants documentaires manquants.")
    operation.finish('metadata')
    operation.start('files', total=len(metadata))
    result = {}
    for file_index, (name, size, expected_hash, count) in enumerate(metadata):
        operation.update('files', current=file_index, total=len(metadata), detail=f'{name} : lecture des fragments')
        if size > MAX_ARTIFACT_BYTES or count != max(1, (size + CHUNK_SIZE - 1) // CHUNK_SIZE):
            raise ValueError("Taille ou découpage de publication invalide.")
        digest = sha256()
        total = 0
        pieces = []
        with connection.cursor() as cursor:
            cursor.execute("SELECT chunk_index,content FROM sme_publication_chunk WHERE publication_id=%s "
                           "AND artifact_name=%s ORDER BY chunk_index", (publication.id, name))
            chunks = cursor.fetchall()
        if len(chunks) != count:
            raise ValueError("Publication incomplète : un fragment du document manque.")
        for expected_index, (index, payload) in enumerate(chunks):
            if index != expected_index or len(payload) > CHUNK_SIZE:
                raise ValueError("Fragments de publication incohérents.")
            digest.update(payload)
            total += len(payload)
            operation.update('files', current=file_index + (expected_index + 1) / max(1, count), total=len(metadata),
                detail=f'{name} : {expected_index + 1}/{count} fragment(s), {total:,}/{size:,} octet(s) lus')
            if name in content or name in ("manifest.json", "donnees.json"):
                pieces.append(payload)
        if total != size or digest.hexdigest() != expected_hash:
            raise ValueError("Publication altérée : " + name)
        if name in ARTIFACTS:
            declared = publication.manifest["artifacts"].get(name, {})
            if declared.get("sha256") != expected_hash or declared.get("size") != size:
                raise ValueError("Le manifeste et les composants de la publication diffèrent.")
        result[name] = {"sha256": expected_hash, "size": size}
        if name == "manifest.json" and _json(b"".join(pieces)) != publication.manifest:
            raise ValueError("Le manifeste archivé diffère du registre central.")
        if name == "donnees.json":
            footprint = dataset_footprint(decode_export_dataset(b"".join(pieces)),
                                          publication.manifest["artifacts"]["modele.xlsx"]["sha256"])
            if footprint != publication.dataset_footprint:
                raise ValueError("L'empreinte logique de la publication diffère de ses données archivées.")
        if name in content:
            result[name]["content"] = b"".join(pieces)
    operation.finish('files', detail=f'{len(metadata)} fichier(s) vérifié(s), manifeste et empreinte logique conformes')
    return result


def register_export(settings, revision: ExportRevision) -> Publication:
    from sme_gend.services.operation_progress import operation_progress
    with operation_progress((('read', 'Vérification de la révision locale'),
                             ('lock', 'Contrôle du registre et de la numérotation'),
                             ('store', 'Enregistrement des fichiers dans le registre central'),
                             ('commit', 'Journalisation et validation de la publication'))) as operation:
        return _register_export_with_progress(settings, revision, operation)


def _register_export_with_progress(settings, revision, operation):
    operation.start('read')
    _allowed("publications.create", export=True)
    require_capability(settings, "publication_registry")
    manifest = verify_export_revision(revision.folder, expected=revision.journal_metadata())
    payloads = {name: (revision.folder / name).read_bytes() for name in FILES}
    for name in ARTIFACTS:
        if sha256(payloads[name]).hexdigest() != manifest["artifacts"][name]["sha256"]:
            raise ValueError("La version locale a changé pendant son enregistrement central.")
    if any(len(payload) > MAX_ARTIFACT_BYTES for payload in payloads.values()):
        raise ValueError("Un composant dépasse la taille maximale d'une publication SME (256 Mo).")
    month = _month(manifest["mois"])
    footprint = dataset_footprint(decode_export_dataset(payloads["donnees.json"]),
                                  manifest["artifacts"]["modele.xlsx"]["sha256"])
    operation.finish('read', detail=f'{len(payloads)} fichier(s), {sum(len(payload) for payload in payloads.values()):,} octet(s)')
    operation.start('lock')
    from sme_gend.services.auth_context import resolve_audit_actor
    result = {}
    with _confirmed_result(result), mysql_import_transaction(settings, read_committed=True):
        with MySQLConnection(settings).connect() as connection:
            sequence, _, _ = _lock_period(connection, month)
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM sme_publication WHERE revision_id=%s", (revision.revision_id,))
                existing = cursor.fetchone()
                operation.finish('lock')
                total_chunks = sum(max(1, (len(payload) + CHUNK_SIZE - 1) // CHUNK_SIZE) for payload in payloads.values())
                operation.start('store', total=total_chunks)
                if existing:
                    publication = _get(connection, int(existing[0]))
                    verified = _verified_artifacts(connection, publication)
                    if publication.mois != month or any(verified[name]["sha256"] != sha256(payload).hexdigest()
                                                       for name, payload in payloads.items()):
                        raise PublicationConflict("Cet identifiant désigne déjà une autre version centrale.")
                    result["publication"] = publication
                else:
                    cursor.execute("INSERT INTO sme_publication(revision_id,mois,revision_no,created_at,created_by,dataset_footprint,manifest) "
                                   "VALUES(%s,%s,%s,UTC_TIMESTAMP(6),%s,%s,%s)",
                                   (revision.revision_id, month, sequence, resolve_audit_actor(), footprint,
                                    json.dumps(manifest, ensure_ascii=False)))
                    identifier = int(cursor.lastrowid)
                    cursor.execute("UPDATE sme_publication_period SET next_revision=next_revision+1,version=version+1 WHERE mois=%s", (month,))
                    stored_chunks = 0
                    for name, payload in payloads.items():
                        count = max(1, (len(payload) + CHUNK_SIZE - 1) // CHUNK_SIZE)
                        cursor.execute("INSERT INTO sme_publication_artifact VALUES(%s,%s,%s,%s,%s)",
                                       (identifier, name, len(payload), sha256(payload).hexdigest(), count))
                        for index in range(count):
                            cursor.execute("INSERT INTO sme_publication_chunk VALUES(%s,%s,%s,%s)",
                                           (identifier, name, index, payload[index * CHUNK_SIZE:(index + 1) * CHUNK_SIZE]))
                            stored_chunks += 1
                            operation.update('store', current=stored_chunks, total=total_chunks,
                                detail=f'{name} : {index + 1}/{count} fragment(s) ; total {stored_chunks}/{total_chunks}')
            operation.finish('store', detail='Révision déjà présente et vérifiée' if existing else 'Tous les fichiers sont enregistrés dans la transaction')
            operation.start('commit', detail='Enregistrement de l’audit et attente de la confirmation MySQL')
            if not existing:
                publication = _get(connection, identifier)
                _audit(settings, "publication_created", publication)
                result["publication"] = publication
    from sme_gend.services.operation_commit_progress import finish_after_commit
    finish_after_commit(settings, operation, 'commit', detail='Révision confirmée dans le registre central')
    return result["publication"]


def import_legacy_revision(settings, folder):
    _allowed("publications.create", export=True)
    manifest = verify_export_revision(Path(folder))
    return register_export(settings, ExportRevision(manifest["revision_id"], Path(folder), manifest))


def list_publications(settings, *, mois=None, status=None, limit=200, offset=0):
    _allowed("publications.view")
    require_capability(settings, "publication_registry")
    if status is not None and status not in STATUSES:
        raise ValueError("État de publication inconnu.")
    if not 1 <= int(limit) <= 1000 or int(offset) < 0:
        raise ValueError("Pagination invalide.")
    predicates, args = [], []
    if mois:
        predicates.append("mois=%s")
        args.append(_month(mois))
    if status:
        predicates.append("status=%s")
        args.append(status)
    sql = "SELECT " + COLUMNS + " FROM sme_publication"
    if predicates:
        sql += " WHERE " + " AND ".join(predicates)
    sql += " ORDER BY mois DESC,revision_no DESC LIMIT %s OFFSET %s"
    with MySQLConnection(settings).connect(read_only=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, (*args, int(limit), int(offset)))
            return [_publication(row) for row in cursor.fetchall()]


def get_publication(settings, publication_id):
    _allowed("publications.view")
    require_capability(settings, "publication_registry")
    with MySQLConnection(settings).connect(read_only=True) as connection:
        return _get(connection, publication_id)


def verify_publication(settings, publication_id):
    _allowed("publications.verify")
    require_capability(settings, "publication_registry")
    with mysql_read_snapshot(settings) as connection:
        publication = _get(connection, publication_id)
        verified = _verified_artifacts(connection, publication)
    return {"publication": publication, "artifacts": verified, "verified": True}


def require_validated_publication(settings, mois, *, publication_id=None, expected_version=None, for_update=False):
    _allowed("publications.view")
    require_capability(settings, "publication_registry")
    if for_update:
        from sme_gend.services.publication_write_barrier import assert_exclusive_publication_barrier
        assert_exclusive_publication_barrier(settings)
    with MySQLConnection(settings).connect(read_only=not for_update) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_validated_id FROM sme_publication_period WHERE mois=%s" +
                           (" FOR UPDATE" if for_update else ""), (_month(mois),))
            row = cursor.fetchone()
        identifier = int(row[0]) if row and row[0] is not None else None
        if identifier is None or (publication_id is not None and identifier != publication_id):
            raise PublicationConflict("Aucune publication validée courante ne correspond au mois choisi.")
        publication = _get(connection, identifier, lock=for_update)
        if (publication.mois != _month(mois) or publication.status != "valide"
                or (expected_version is not None and publication.version != expected_version)):
            raise PublicationConflict("La publication courante a changé ; actualisez la liste.")
        _verified_artifacts(connection, publication)
        return publication


def assert_publication_current_data(settings, publication, *, for_update=True):
    from sme_gend.services.operation_progress import operation_progress
    with operation_progress((('projection', 'Actualisation des situations concernées'),
                             ('archive', 'Contrôle du modèle et des fichiers archivés'),
                             ('capture', 'Lecture cohérente des données du mois'),
                             ('compare', 'Comparaison avec la révision documentaire'))) as operation:
        return _assert_publication_current_data_with_progress(settings, publication,
            for_update=for_update, operation=operation)


def _assert_publication_current_data_with_progress(settings, publication, *, for_update, operation):
    operation.start('projection')
    _allowed("publications.verify")
    from sme_gend.services.publication_write_barrier import assert_exclusive_publication_barrier
    assert_exclusive_publication_barrier(settings)
    from sme_gend.services.month_projection import refresh_month_projection
    refresh_month_projection(settings, publication.mois)
    operation.finish('projection')
    operation.start('archive')
    from dataclasses import replace as settings_replace
    from openpyxl import load_workbook
    from sme_gend.services.numerique_excel_export import _capture_numerique_workbook_data, _parse_grade_columns
    with MySQLConnection(settings).connect() as connection:
        current = _get(connection, publication.id, lock=for_update)
        if current.version != publication.version:
            raise PublicationConflict("La publication a changé ; actualisez la liste.")
        artifacts = _verified_artifacts(connection, current, content=("modele.xlsx",))
    operation.finish('archive')
    operation.start('capture')
    model = load_workbook(BytesIO(artifacts["modele.xlsx"]["content"]), read_only=True, data_only=True)
    try:
        grades = _parse_grade_columns(model["NUMERIQUE"])[0]
    finally:
        model.close()
    scoped = settings_replace(settings, mysql_view_mois=publication.mois)
    dataset, _ = _capture_numerique_workbook_data(scoped, publication.mois, grades, scoped, join_transaction=True)
    operation.finish('capture')
    operation.start('compare')
    footprint = dataset_footprint(dataset, artifacts["modele.xlsx"]["sha256"])
    if footprint != current.dataset_footprint:
        raise PublicationDataChanged("Les données du mois ont changé depuis cet export. Créez une nouvelle révision SME.")
    operation.finish('compare', detail='Données du mois conformes à la version documentaire')
    return footprint


def _approve(settings, publication_id, *, expected_version, expected_current_id, reason, replacement):
    from sme_gend.services.operation_progress import operation_progress
    with operation_progress((('context', 'Contrôle des droits et de la version courante'),
                             ('verify', 'Vérification des fichiers et des données du mois'),
                             ('update', 'Enregistrement de la validation documentaire'),
                             ('commit', 'Journalisation et validation de la transaction'))) as operation:
        return _approve_with_progress(settings, publication_id, expected_version=expected_version,
            expected_current_id=expected_current_id, reason=reason, replacement=replacement, operation=operation)


def _approve_with_progress(settings, publication_id, *, expected_version, expected_current_id, reason, replacement, operation):
    operation.start('context')
    _allowed("publications.replace" if replacement else "publications.validate", admin=True)
    require_capability(settings, "publication_registry")
    if replacement and not str(reason or "").strip():
        raise ValueError("Le motif de remplacement est obligatoire.")
    if len(str(reason or "")) > 2000:
        raise ValueError("Le motif dépasse 2 000 caractères.")
    from sme_gend.services.publication_write_barrier import publication_write_barrier
    from sme_gend.services.auth_context import resolve_audit_actor
    result = {}
    with _confirmed_result(result), publication_write_barrier(settings, exclusive=True):
        with MySQLConnection(settings).connect() as connection:
            publication = _get(connection, publication_id)
            _, current_id, _ = _lock_period(connection, publication.mois)
            publication = _get(connection, publication_id, lock=True)
            if publication.status != "brouillon" or publication.version != expected_version:
                raise PublicationConflict("Cette révision n'est plus le brouillon chargé ; actualisez la liste.")
            if current_id != expected_current_id or (replacement and current_id is None) or (not replacement and current_id is not None):
                raise PublicationConflict("La publication validée du mois a changé. Choisissez un remplacement explicite.")
            operation.finish('context')
            operation.start('verify')
            assert_publication_current_data(settings, publication)
            operation.finish('verify')
            operation.start('update')
            with connection.cursor() as cursor:
                if replacement:
                    previous = _get(connection, int(current_id), lock=True)
                    if previous.status != "valide" or previous.mois != publication.mois:
                        raise PublicationConflict("La version précédente n'est plus validée.")
                    cursor.execute("UPDATE sme_publication SET status='remplace',version=version+1,replaced_by_id=%s WHERE id=%s", (publication.id, current_id))
                cursor.execute("UPDATE sme_publication SET status='valide',version=version+1,validated_at=UTC_TIMESTAMP(6),"
                               "validated_by=%s,validation_note=%s WHERE id=%s", (resolve_audit_actor(), str(reason or "").strip(), publication.id))
                cursor.execute("UPDATE sme_publication_period SET current_validated_id=%s,version=version+1 WHERE mois=%s", (publication.id, publication.mois))
            publication = _get(connection, publication.id)
            operation.finish('update')
            operation.start('commit', detail='Journalisation et attente de la confirmation MySQL')
            _audit(settings, "publication_replaced" if replacement else "publication_validated", publication,
                   previous_publication_id=current_id, reason=str(reason or "").strip())
            result["publication"] = publication
    from sme_gend.services.operation_commit_progress import finish_after_commit
    finish_after_commit(settings, operation, 'commit', detail='Remplacement confirmé' if replacement else 'Validation confirmée')
    return result["publication"]


def validate_publication(settings, publication_id, *, expected_version, expected_current_id=None, note=""):
    return _approve(settings, publication_id, expected_version=expected_version,
                    expected_current_id=expected_current_id, reason=note, replacement=False)


def replace_publication(settings, publication_id, *, expected_version, expected_current_id, reason):
    return _approve(settings, publication_id, expected_version=expected_version,
                    expected_current_id=expected_current_id, reason=reason, replacement=True)


def download_publication(settings, publication_id, destination):
    from sme_gend.services.operation_progress import operation_progress
    with operation_progress((('read', 'Lecture et vérification de la publication centrale'),
                             ('copy', 'Copie du classeur'),
                             ('verify', 'Vérification de la copie'),
                             ('publish', 'Enregistrement du fichier'),
                             ('audit', 'Finalisation du téléchargement'))) as operation:
        return _download_publication_with_progress(settings, publication_id, destination, operation)


def _download_publication_with_progress(settings, publication_id, destination, operation):
    operation.start('read')
    _allowed("publications.download", export=True)
    require_capability(settings, "publication_registry")
    from sme_gend.services.sme_export_revision import validate_export_destination
    template = settings.template_numerique_path or Path(destination).parent / ".modele_sme_protege"
    from sme_gend.services.sme_publication_backup import storage_root
    target = validate_export_destination(destination, template=template, root=storage_root())
    with mysql_read_snapshot(settings) as connection:
        publication = _get(connection, publication_id)
        artifacts = _verified_artifacts(connection, publication, content=("classeur.xlsx",))
    operation.finish('read')
    operation.start('copy', total=artifacts['classeur.xlsx']['size'])
    from sme_gend.services.safe_paths import atomic_destination
    with atomic_destination(target) as staged:
        staged.write_bytes(artifacts["classeur.xlsx"]["content"])
        operation.finish('copy', detail=f'{artifacts["classeur.xlsx"]["size"]:,} octet(s) copiés')
        operation.start('verify')
        if sha256(staged.read_bytes()).hexdigest() != artifacts["classeur.xlsx"]["sha256"]:
            raise ValueError("Copie du classeur SME incomplète ; le fichier précédent est conservé.")
        operation.finish('verify')
        operation.start('publish', detail='Enregistrement atomique du classeur')
    operation.finish('publish')
    operation.start('audit')
    try:
        _audit(settings, "publication_downloaded", publication, destination=str(target))
    except Exception:
        publication = replace(publication, operation_warning="Le fichier est enregistré ; sa trace de téléchargement reste à vérifier.")
    operation.finish('audit', detail=publication.operation_warning or 'Téléchargement terminé')
    return publication


def validate_registry_snapshot(settings):
    """Restore verifier: no permissions, DDL, approvals or live-data comparison."""
    with MySQLConnection(settings).connect(read_only=True) as connection:
        from sme_gend.services.schema_contracts import inspect_schema, errors
        from sme_gend.services.schema_migrations import LEDGER
        shape_errors = errors((LEDGER, *PUBLICATION_TABLES), inspect_schema(connection))
        if shape_errors:
            raise ValueError("Schéma du registre documentaire incompatible : " + "; ".join(shape_errors[:10]))
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM sme_publication_artifact a LEFT JOIN sme_publication p "
                           "ON p.id=a.publication_id WHERE p.id IS NULL")
            if cursor.fetchone()[0]:
                raise ValueError("Composants de publication orphelins.")
            cursor.execute("SELECT COUNT(*) FROM sme_publication_chunk c LEFT JOIN sme_publication_artifact a "
                           "ON a.publication_id=c.publication_id AND a.artifact_name=c.artifact_name WHERE a.publication_id IS NULL")
            if cursor.fetchone()[0]:
                raise ValueError("Fragments de publication orphelins.")
            cursor.execute("SELECT " + COLUMNS + " FROM sme_publication ORDER BY id")
            publications = [_publication(row) for row in cursor.fetchall()]
            cursor.execute("SELECT mois,next_revision,current_validated_id FROM sme_publication_period")
            periods = cursor.fetchall()
        by_id = {publication.id: publication for publication in publications}
        known_months = {row[0] for row in periods}
        verified = 0
        for publication in publications:
            if publication.mois not in known_months:
                raise ValueError("Publication rattachée à une période documentaire absente.")
            _verified_artifacts(connection, publication)
            if publication.status == "brouillon":
                if publication.validated_at is not None or publication.validated_by or publication.replaced_by_id is not None:
                    raise ValueError("Un brouillon contient une validation incohérente.")
            elif publication.validated_at is None or not publication.validated_by:
                raise ValueError("La validation d'une publication ne possède pas d'auteur ou de date.")
            if publication.status == "valide" and publication.replaced_by_id is not None:
                raise ValueError("Une publication courante ne peut pas être déjà remplacée.")
            verified += 1
        for month, next_number, current_id in periods:
            same = [publication for publication in publications if publication.mois == month]
            active = [publication.id for publication in same if publication.status == "valide"]
            numbers = sorted(publication.revision_no for publication in same)
            if (active != ([] if current_id is None else [current_id])
                    or numbers != list(range(1, len(same) + 1)) or next_number != len(same) + 1):
                raise ValueError("Registre des publications incohérent : révision courante ou numérotation invalide.")
        for publication in publications:
            if publication.status == "remplace":
                replacement = by_id.get(publication.replaced_by_id)
                if (replacement is None or replacement.mois != publication.mois
                        or replacement.status not in ("valide", "remplace")
                        or replacement.revision_no <= publication.revision_no):
                    raise ValueError("Chaîne de remplacement de publication invalide.")
    return {"publications": verified, "verified": True}
