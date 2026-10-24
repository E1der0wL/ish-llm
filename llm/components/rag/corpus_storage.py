"""RAG corpus files, immutable publication and explicit generation retention.

The selected Component supplies validation and extension callbacks; this Owner
uses the existing workspace transaction and never selects deletion targets.
"""

import shutil
from uuid import uuid4
from llm.components.base import validate_name
from llm.services.infrastructure.storage import (
    atomic_json,
    read_json,
    remove_named_tree,
    recover_deletions,
    revision_token,
    tree_snapshot,
    check_revision,
    DeletionJournal,
)
from llm.services.infrastructure.logging import log_event
from .errors import RAGConflictError
from .corpus_records import RAGCorpus
from .document_records import RAGDocument
from .publication_records import (
    CorpusIdentity,
    CorpusPointer,
    CorpusControl,
    CorpusCatalog,
    GenerationRow,
    GenerationCompactionPlan,
)
from .retrieval_records import CatalogRequest, CatalogCursor, CatalogPage
from .indexing import build_index, update_index, ChromaIndex
from .graph_indexing import build_graph, update_graph, GraphStore
from .ingestion_records import EmbeddingEvidence
from .cache import SnapshotCache
from .files import copy_file


class PublicationObservation:
    VALIDATING = "validating"
    INDEXING = "indexing"
    WRITING_METADATA = "writing_metadata"
    PUBLISHING = "publishing"
    CLEANUP_PENDING = "rag.cleanup_pending"
    PUBLISHED = "rag.published"


class CorpusStorage:
    BACKUP_INDEXES = "RAG backup requires matching vector and graph generation"
    ABSENT = None
    TREE_GLOB = "*"
    REGULAR_EVIDENCE = "RAG evidence requires a regular file: {name}"
    EMPTY_POINTER_CONFLICT = "Empty RAG pointer conflicts with stored generations"
    MISSING_ACTIVE_CORPUS = "RAG active generation requires its corpus"
    REGULAR_SOURCE = "RAG source requires a regular file: {name}"
    MISSING_VECTOR_INDEX = "RAG generation requires its vector index"
    MISSING_GRAPH_INDEX = "RAG generation requires graph.kuzu"
    CORPUS_CHANGED = "RAG corpus integrity mismatch"
    CATALOG_CHANGED = "RAG catalog disagrees with its published corpus"
    PUBLICATION_CONFLICT = "Corpus changed during preparation; reload and retry explicitly"
    INVALID_DOCUMENTS = "RAG corpus documents must be an object"
    MISSING_PROFILE = "RAG document requires its embedding profile"
    PENDING_DELETION = "Pending RAG generation deletion must be recovered first"
    INVALID_RETIRED_ENTRIES = "Retired RAG generation has incomplete or unknown entries"
    RETIRED_CATALOG_CHANGED = "Retired RAG catalog disagrees with its source corpus"
    MISSING_RETIRED_INDEXES = "Retired RAG generation requires its stored indexes"
    EXPLICIT_GENERATIONS = "RAG compaction requires an explicit sequence of generations"
    DUPLICATE_TARGETS = "RAG compaction targets must be unique"
    MISSING_PLAN_VERSION = "RAG compaction requires a current plan version"
    INELIGIBLE_TARGET = "RAG compaction cannot delete protected or unknown generations"
    IDENTITY_FILE = "identity.json"
    ACTIVE_FILE = "active.json"
    CONTROL_FILE = "corpus.manifest.json"
    CORPUS_FILE = "corpus.json"
    CATALOG_FILE = "catalog.json"
    GENERATIONS_DIRECTORY = "generations"

    @staticmethod
    def create(component, project, root):
        identity = uuid4().hex
        atomic_json(root / CorpusStorage.IDENTITY_FILE, CorpusIdentity.create(identity))
        atomic_json(root / CorpusStorage.ACTIVE_FILE, CorpusPointer.create(RAGCorpus.ABSENT))
        component._write_control(
            project, identity, CorpusStorage.ABSENT, CorpusStorage.ABSENT, CorpusStorage.ABSENT
        )

    @staticmethod
    def _generation_path(component, project, generation):
        component._validate_identity(generation)
        return component._checked(component.root(project) / CorpusStorage.GENERATIONS_DIRECTORY / generation)

    @staticmethod
    def _checked_tree(component, path):
        component._checked(path)
        for entry in path.rglob(CorpusStorage.TREE_GLOB):
            component._checked(entry)
        return path

    @staticmethod
    def _build(component, path, documents):
        build_index(path / ChromaIndex.DIRECTORY, documents, batch_size=component.index_batch_size)
        build_graph(path / GraphStore.FILE_NAME, documents, options=component.graph_options)

    @staticmethod
    def _read_evidence(component, path):
        path = component._checked(path)
        if not path.is_file():
            raise FileNotFoundError(CorpusStorage.REGULAR_EVIDENCE.format(name=path.name))
        return read_json(path)

    @staticmethod
    def _write_control(component, project, identity, generation, corpus, catalog):
        atomic_json(
            component.root(project) / CorpusStorage.CONTROL_FILE,
            CorpusControl.create(project.id, component.name, identity, generation, corpus, catalog),
        )

    @staticmethod
    def _corpus_control(component, project):
        """Read the current owner proof without parsing the immutable corpus."""
        root = component.root(project)
        identity = component._read_evidence(root / CorpusStorage.IDENTITY_FILE)
        active = component._read_evidence(root / CorpusStorage.ACTIVE_FILE)
        CorpusIdentity.validate(identity)
        CorpusPointer.validate(active)
        control = component._read_evidence(root / CorpusStorage.CONTROL_FILE)
        CorpusControl.validate(control, project.id, component.name, identity, active)
        identity = CorpusIdentity(identity).identifier
        component._validate_identity(identity)
        generation = CorpusPointer(active).generation
        CorpusControl.validate_revisions(control, generation)
        if generation is CorpusStorage.ABSENT:
            generations = component._checked(root / CorpusStorage.GENERATIONS_DIRECTORY)
            if generations.exists() and any(generations.iterdir()):
                raise ValueError(CorpusStorage.EMPTY_POINTER_CONFLICT)
        else:
            path = component._generation_path(project, generation)
            if not component._checked(path / CorpusStorage.CORPUS_FILE).is_file():
                raise FileNotFoundError(CorpusStorage.MISSING_ACTIVE_CORPUS)
        return identity, generation, control

    @staticmethod
    def corpus_version(component, project):
        """잠금 안에서 작은 포인터와 연결 증거만 읽는다."""
        identity, generation, _ = component._corpus_control(project)
        return identity, generation

    @staticmethod
    def source_version(component, project):
        """Bind a read snapshot across model calls without parsing the corpus again."""
        identity, generation, control = component._corpus_control(project)
        version = (
            identity,
            generation,
            CorpusControl(control).corpus_revision,
            CorpusControl(control).catalog_revision,
        )
        if generation is CorpusStorage.ABSENT:
            return version
        path = component._generation_path(project, generation)
        for name in (CorpusStorage.CORPUS_FILE, CorpusStorage.CATALOG_FILE):
            source = component._checked(path / name)
            if not source.is_file():
                raise FileNotFoundError(CorpusStorage.REGULAR_SOURCE.format(name=name))
            info = source.stat()
            version += ((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns),)
        if not component._checked(path / ChromaIndex.DIRECTORY).is_dir():
            raise FileNotFoundError(CorpusStorage.MISSING_VECTOR_INDEX)
        if not component._checked(path / GraphStore.FILE_NAME).is_file():
            raise FileNotFoundError(CorpusStorage.MISSING_GRAPH_INDEX)
        return version

    @staticmethod
    def _catalog(component, project, generation, control):
        rows = component._read_evidence(
            component._generation_path(project, generation) / CorpusStorage.CATALOG_FILE
        )
        return CorpusCatalog.validate(rows, control)

    @staticmethod
    def snapshot(component, project):
        identity, generation, control = component._corpus_control(project)
        if generation is CorpusStorage.ABSENT:
            return RAGCorpus.empty_snapshot(identity)
        path = component._generation_path(project, generation)
        source = component._checked(path / CorpusStorage.CORPUS_FILE)
        info = source.stat()  # Deletion/replacement must never be hidden by a cached generation.
        key = (
            str(source),
            identity,
            generation,
            CorpusControl(control).corpus_revision,
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )
        # Missing means inactive, not a tuning default or a materialized configuration value.
        maximum = (
            SnapshotCache.EMPTY_BYTES
            if component.snapshot_cache_max_bytes is CorpusStorage.ABSENT
            else component.snapshot_cache_max_bytes
        )
        value = component.snapshot_cache.get(key, maximum)
        cached = value is not CorpusStorage.ABSENT
        if not cached:
            value = component.validate_corpus(read_json(source))
            if revision_token(value) != CorpusControl(control).corpus_revision:
                raise ValueError(CorpusStorage.CORPUS_CHANGED)
        catalog = component._catalog(project, generation, control)
        if revision_token(catalog) != revision_token(component._catalog_rows(RAGCorpus(value).documents)):
            raise ValueError(CorpusStorage.CATALOG_CHANGED)
        if not component._checked(path / ChromaIndex.DIRECTORY).is_dir():
            raise FileNotFoundError(CorpusStorage.MISSING_VECTOR_INDEX)
        if not component._checked(path / GraphStore.FILE_NAME).is_file():
            raise FileNotFoundError(CorpusStorage.MISSING_GRAPH_INDEX)
        if not cached:
            component.snapshot_cache.put(key, value, maximum)
        return RAGCorpus.snapshot(value, identity, generation)

    @staticmethod
    def list_catalog(component, project, *, limit, cursor=CatalogRequest.ABSENT):
        """현재 세대에 함께 공개된 설명 목록만 읽는다. 원본은 corpus가 소유한다."""
        if type(limit) is not int or limit < CatalogRequest.MINIMUM_LIMIT:
            raise ValueError(CatalogRequest.INVALID_LIMIT)
        identity, generation, control = component._corpus_control(project)
        after = CatalogRequest.FIRST_AFTER
        if cursor is not CatalogRequest.ABSENT:
            CatalogCursor.validate(cursor)
            if CatalogCursor(cursor).binding != (identity, generation):
                raise RAGConflictError(CatalogCursor.CONFLICT)
            after = validate_name(CatalogCursor(cursor).after)
        rows = [] if generation is CatalogRequest.ABSENT else component._catalog(project, generation, control)
        selected = [row for row in rows if RAGDocument(row).identifier > after][
            : limit + CatalogRequest.LOOKAHEAD
        ]
        more = len(selected) > limit
        selected = selected[:limit]
        return CatalogPage.create(
            selected,
            generation,
            (
                CatalogCursor.create(
                    identity, generation, RAGDocument(selected[CatalogRequest.LAST_ROW]).identifier
                )
                if more
                else CatalogRequest.ABSENT
            ),
        )

    @staticmethod
    def publish(component, project, previous, documents, *, progress=ABSENT):
        """DB를 닫고 포인터·필수 증거를 같은 workspace transaction으로 확정한다."""
        from .component import RAGComponent

        current = component.snapshot(project)
        if (RAGCorpus(current).identity, RAGCorpus(current).generation) != (
            RAGCorpus(previous).identity,
            RAGCorpus(previous).generation,
        ):
            raise RAGConflictError(CorpusStorage.PUBLICATION_CONFLICT)
        if progress:
            progress(PublicationObservation.VALIDATING)
        if not isinstance(documents, dict):
            raise ValueError(CorpusStorage.INVALID_DOCUMENTS)
        profile = CorpusStorage.ABSENT
        if documents:
            first = next(iter(documents.values()))
            if not isinstance(first, dict) or EmbeddingEvidence.Field.PROFILE not in first:
                raise ValueError(CorpusStorage.MISSING_PROFILE)
            profile = EmbeddingEvidence(first).profile_record
        corpus = component.validate_corpus(RAGCorpus.create(documents, profile))
        generation = uuid4().hex
        path = component._generation_path(project, generation)
        from llm.services.infrastructure.storage import make_directory

        if path.exists():
            raise FileExistsError(path)
        make_directory(path)
        try:
            if progress:
                progress(PublicationObservation.INDEXING)
            if (
                RAGCorpus(previous).generation
                and type(component)._build is RAGComponent._build
                and RAGComponent._build.__name__ not in component.__dict__
            ):
                source = component._checked_tree(
                    component._generation_path(project, RAGCorpus(previous).generation)
                )
                shutil.copytree(
                    source / ChromaIndex.DIRECTORY, path / ChromaIndex.DIRECTORY, copy_function=copy_file
                )
                copy_file(source / GraphStore.FILE_NAME, path / GraphStore.FILE_NAME)
                update_index(
                    path / ChromaIndex.DIRECTORY,
                    RAGCorpus(previous).documents,
                    documents,
                    batch_size=component.index_batch_size,
                )
                update_graph(
                    path / GraphStore.FILE_NAME,
                    RAGCorpus(previous).documents,
                    documents,
                    options=component.graph_options,
                )
            else:
                component._build(path, documents)
            if progress:
                progress(PublicationObservation.WRITING_METADATA)
            catalog = component._catalog_rows(documents)
            atomic_json(path / CorpusStorage.CATALOG_FILE, catalog)
            atomic_json(path / CorpusStorage.CORPUS_FILE, corpus)
        except BaseException:
            remove_named_tree(path.parent, path, generation)
            raise
        # 포인터 교체 뒤 디렉토리 fsync가 실패할 수도 있다. 공개를 시도한 세대는
        # 오류가 나더라도 여기서 지우지 않아 active가 없는 DB를 가리키지 않게 한다.
        if progress:
            progress(PublicationObservation.PUBLISHING)
        atomic_json(component.root(project) / CorpusStorage.ACTIVE_FILE, CorpusPointer.create(generation))
        component._write_control(project, RAGCorpus(previous).identity, generation, corpus, catalog)
        # Finish only previously committed deletion intents; publication never selects old generations.
        try:
            component.recover_compaction(project)
        except (OSError, ValueError):
            log_event(project.paths.logs, PublicationObservation.CLEANUP_PENDING, entity_id=project.id)
        log_event(
            project.paths.logs, PublicationObservation.PUBLISHED, entity_id=project.id, count=len(documents)
        )

    @staticmethod
    def recover_compaction(component, project):
        """Complete existing deletion intents without selecting new targets."""
        active = RAGCorpus(component.snapshot(project)).generation
        root = component._checked(component.root(project) / CorpusStorage.GENERATIONS_DIRECTORY)
        return recover_deletions(root, protected=(active,))

    @staticmethod
    def compaction_plan(component, project):
        """Inspect eligible generations; Application alone selects which ones to discard."""
        current = component.snapshot(project)
        root = component._checked(component.root(project) / CorpusStorage.GENERATIONS_DIRECTORY)
        journal = component._checked(root / DeletionJournal.DIRECTORY)
        if journal.exists() and any(journal.iterdir()):
            raise ValueError(CorpusStorage.PENDING_DELETION)
        candidates, protected = [], []
        for path in sorted(root.iterdir()) if root.exists() else ():
            if path.name == journal.name:
                continue
            generation = path.name
            component._generation_path(project, generation)
            if generation != RAGCorpus(current).generation:
                if {entry.name for entry in path.iterdir()} != {
                    CorpusStorage.CORPUS_FILE,
                    CorpusStorage.CATALOG_FILE,
                    ChromaIndex.DIRECTORY,
                    GraphStore.FILE_NAME,
                }:
                    raise ValueError(CorpusStorage.INVALID_RETIRED_ENTRIES)
                corpus = component.validate_corpus(component._read_evidence(path / CorpusStorage.CORPUS_FILE))
                catalog = component._read_evidence(path / CorpusStorage.CATALOG_FILE)
                if revision_token(catalog) != revision_token(
                    component._catalog_rows(RAGCorpus(corpus).documents)
                ):
                    raise ValueError(CorpusStorage.RETIRED_CATALOG_CHANGED)
                if (
                    not component._checked(path / ChromaIndex.DIRECTORY).is_dir()
                    or not component._checked(path / GraphStore.FILE_NAME).is_file()
                ):
                    raise ValueError(CorpusStorage.MISSING_RETIRED_INDEXES)
            files = tree_snapshot(path)
            row = GenerationRow.create(generation, files)
            (protected if generation == RAGCorpus(current).generation else candidates).append(row)
        return GenerationCompactionPlan.create(
            RAGCorpus(current).identity, RAGCorpus(current).generation, candidates, protected
        )

    @staticmethod
    def compact(component, project, generations, *, expected_version):
        if not isinstance(generations, (list, tuple)):
            raise ValueError(CorpusStorage.EXPLICIT_GENERATIONS)
        for generation in generations:
            component._validate_identity(generation)
        if len(generations) != len(set(generations)):
            raise ValueError(CorpusStorage.DUPLICATE_TARGETS)
        if not isinstance(expected_version, str) or not expected_version:
            raise ValueError(CorpusStorage.MISSING_PLAN_VERSION)
        plan = component.compaction_plan(project)
        current = GenerationCompactionPlan(plan).evidence
        check_revision(current, expected_version)
        eligible = GenerationCompactionPlan(plan).eligible
        if any(generation not in eligible for generation in generations):
            raise ValueError(CorpusStorage.INELIGIBLE_TARGET)
        root = component._checked(component.root(project) / CorpusStorage.GENERATIONS_DIRECTORY)
        for generation in generations:
            remove_named_tree(root, component._generation_path(project, generation), generation)

    @staticmethod
    def validate_backup(component, project, snapshot):
        generation = RAGCorpus(snapshot).generation
        if generation is not CorpusStorage.ABSENT:
            path = component._generation_path(project, generation)
            if not (path / ChromaIndex.DIRECTORY).is_dir() or not (path / GraphStore.FILE_NAME).is_file():
                raise ValueError(CorpusStorage.BACKUP_INDEXES)
