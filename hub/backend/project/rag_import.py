"""Selected files become text documents; Vision retains image provenance."""

from llm.core.editable_snapshot import EditableSnapshot

from llm.core.implementation_parameters import ImplementationParameters

from llm.core.configuration_views import ConfigurationView

import asyncio

from llm.providers.runtime import diagnostic_failure
from llm.components.vision.records import VisionImageRecord, VisionImageIdentity
from llm.components.vision.results import VisionExtraction
from llm.components.vision.configuration import VisionSettings
from llm.providers.request_values import ModelParameters
from llm.components.rag.document_records import RAGDocumentInput
from .management_values import ImportProvenance
from .rag_views import ImageImport, RAGEntry, RAGOperation, DocumentExcerpt


def document_description(text, maximum=DocumentExcerpt.MAXIMUM):
    """A cheap editable excerpt, not a model-generated or verified summary."""
    value = DocumentExcerpt.SEPARATOR.join(text.split())
    return (
        value
        if len(value) <= maximum
        else value[: maximum - DocumentExcerpt.ELLIPSIS_LENGTH]
        + DocumentExcerpt.ELLIPSIS
    )


async def read_document(manager, argument, *, progress=RAGOperation.ABSENT):
    path = ImageImport(argument).path if isinstance(argument, dict) else argument
    source = manager.source_path(path)
    identifier = manager.import_identifier(source)
    # Validate before any Vision import/model work or document parsing.
    await manager._require_new(identifier)
    if source.suffix.lower() not in ImageImport.SUFFIXES:
        if isinstance(argument, dict):
            raise ValueError(ImageImport.INVALID_IMAGE)
        identifier, values = await asyncio.to_thread(manager._read_import, path)
    else:
        if (
            not isinstance(argument, dict)
            or ImageImport(argument).optional_mode not in ImageImport.MODES
        ):
            raise ValueError(ImageImport.SELECT_MODE)
        if (
            manager.project is RAGOperation.ABSENT
            or VisionImageIdentity.COMPONENT
            not in (await manager.project.aget_data()).components
        ):
            raise ValueError(ImageImport.ENABLE_VISION)
        vision = await manager.project.components.aget(VisionImageIdentity.COMPONENT)
        config = ImplementationParameters.read(
            ConfigurationView.read(await vision.aresolve_config()).values
        ).config
        mode = ImageImport(argument).mode
        if (
            mode == VisionExtraction.OCR
            and not VisionSettings(config).ocr.optional_backend
        ):
            raise ValueError(ImageImport.SELECT_OCR)
        if (
            mode == VisionExtraction.ANALYSIS
            and not ModelParameters(VisionSettings(config).completion).optional_model
        ):
            raise ValueError(ImageImport.SELECT_MODEL)
        if progress:
            progress(RAGEntry.preparation(mode))
        image = await vision.aimport_image(
            source, title=source.name, metadata=ImportProvenance.create(source)
        )
        try:
            options = ImageImport.options(mode)
            values = await vision.aextract_document(
                VisionImageRecord(image).identifier,
                mode=mode,
                title=source.name,
                **options
            )
        except BaseException:
            # Public deletion tombstones only this newly imported image. Vision
            # owns bytes; no filesystem cleanup or removal of previous sources.
            try:
                snapshot = EditableSnapshot.read(
                    await vision.asnapshot(VisionImageRecord(image).identifier)
                )
                await vision.adelete(
                    VisionImageRecord(image).identifier,
                    expected_version=snapshot.version,
                )
            except Exception as cleanup:
                diagnostic_failure(
                    cleanup,
                    operation=RAGOperation.IMAGE_CLEANUP,
                    stage=RAGOperation.CLEANUP_STAGE,
                )
            raise
        ImportProvenance.stamp_document(values, source)
    RAGDocumentInput.ensure_description(values, document_description)
    return identifier, values
