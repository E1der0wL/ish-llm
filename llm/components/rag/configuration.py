"""RAG-owned explicit settings, model binding and configuration projections."""

from copy import copy, deepcopy
from llm.core.configuration import ConfigurationSource, resolve_config
from llm.core.configuration_views import ConfigurationView, RuntimeConfiguration
from llm.core.implementation_parameters import ImplementationParameters
from llm.core.models import ProjectConfig
from llm.core.parameters import ParameterLayout
from llm.core.schema import SchemaType, SchemaExtension, OpenSchemaKind
from llm.components.prompts.records import PromptDefinition
from llm.providers.requests import provider_schema
from llm.services.infrastructure.storage import revision_token
from ._client import ModelClient
from .prompts import ExtractionOptions, extraction_schema
from .search import SearchOptions, search_schema
from .job_policy import JobAdmission


class RAGConfiguration:
    ABSENT = None
    SOURCE_ATTRIBUTE = "_configuration_source"
    OPTIONS_SUFFIX = "kwargs"
    CLIENT_PROVIDER_ATTRIBUTE = "provider_options"
    CLIENT_ENFORCED_ATTRIBUTE = "enforced_configuration"
    CLIENT_PROMPT_ATTRIBUTE = "prompt"
    CLIENT_EXTRACTION_METHOD = "with_extraction"
    CUSTOM_SCHEMA_SUFFIX = ".with_config"
    RETIRED_BATCH_FIELDS = ("embedding_batch_size", "embedding_batching")
    MISSING_PROMPT = "RAG prompt_id requires the selected prompts component through its Project handle"
    CUSTOM_PARAMETERS = "Custom {name} requires with_config(params) to apply project parameters"
    CUSTOM_EXTRACTION = (
        "Custom extractor requires with_extraction(options, prompt) to apply extraction policies"
    )
    RETIRED_BATCH = (
        "Embedding batch configuration were removed; use embedding_concurrency and embedding_cache_max_bytes"
    )
    INVALID = "Invalid RAG configuration: "
    INTEGER_REQUIRED = "{name} requires an integer"
    QUERY_CACHE_DESCRIPTION = "Query vector cache capacity; absent or zero disables caching"
    SNAPSHOT_CACHE_DESCRIPTION = "Corpus projection cache capacity; absent or zero disables caching"
    CHUNK_SIZE_MINIMUM = 1
    CONCURRENCY_MINIMUM = 1
    CACHE_BYTES_MINIMUM = 0
    EXTRACTION_BATCH_MINIMUM = 1
    INDEX_BATCH_MINIMUM = 1
    GRAPH_BUFFER_MINIMUM = 1
    GRAPH_THREADS_MINIMUM = 1

    class Field:
        CHUNK_SIZE = "chunk_size"
        EMBEDDING_CONCURRENCY = "embedding_concurrency"
        EMBEDDING_CACHE_MAX_BYTES = "embedding_cache_max_bytes"
        QUERY_CACHE_MAX_BYTES = "query_cache_max_bytes"
        SNAPSHOT_CACHE_MAX_BYTES = "snapshot_cache_max_bytes"
        EXTRACTION_BATCH_SIZE = "extraction_batch_size"
        SEARCH_CACHE_CHARS = "search_cache_chars"
        INDEX_BATCH_SIZE = "index_batch_size"
        GRAPH = "graph"
        DOCUMENT_KWARGS = "document_kwargs"
        QUERY_KWARGS = "query_kwargs"
        EMBEDDING_PARAMS = "embedding_params"
        EXTRACTION_PARAMS = "extraction_params"
        RERANK_PARAMS = "rerank_params"
        PROVIDER = "provider"
        BUFFER_POOL_SIZE = "buffer_pool_size"
        MAX_NUM_THREADS = "max_num_threads"

    class Model:
        EMBEDDING = "embedding"
        EXTRACTOR = "extractor"
        RERANKER = "reranker"
        EMBEDDING_ID = "embedding_id"

    POLICY_FIELDS = (Field.EMBEDDING_CONCURRENCY, Field.PROVIDER, JobAdmission.KEY)
    BOUND_FIELDS = (
        Field.CHUNK_SIZE,
        Field.EMBEDDING_CONCURRENCY,
        Field.EMBEDDING_CACHE_MAX_BYTES,
        Field.QUERY_CACHE_MAX_BYTES,
        Field.SNAPSHOT_CACHE_MAX_BYTES,
        Field.EXTRACTION_BATCH_SIZE,
        Field.SEARCH_CACHE_CHARS,
        Field.DOCUMENT_KWARGS,
        Field.QUERY_KWARGS,
        Field.INDEX_BATCH_SIZE,
    )
    LAYOUT = ParameterLayout(
        config=(
            Field.CHUNK_SIZE,
            Field.EMBEDDING_CACHE_MAX_BYTES,
            Field.QUERY_CACHE_MAX_BYTES,
            Field.SNAPSHOT_CACHE_MAX_BYTES,
            Field.EXTRACTION_BATCH_SIZE,
            Field.SEARCH_CACHE_CHARS,
            Field.INDEX_BATCH_SIZE,
            SearchOptions.CONFIG_KEY,
            Field.GRAPH,
            Field.DOCUMENT_KWARGS,
            Field.QUERY_KWARGS,
            Field.EMBEDDING_PARAMS,
            Field.EXTRACTION_PARAMS,
            Field.RERANK_PARAMS,
        ),
        policy=POLICY_FIELDS,
        paths={
            **{
                ExtractionOptions.KEY
                + "."
                + key: ImplementationParameters.CONFIG
                + "."
                + ExtractionOptions.KEY
                + "."
                + key
                for key in (
                    ExtractionOptions.Field.JSON_MODE,
                    ExtractionOptions.Field.PROMPT_ID,
                    ExtractionOptions.Field.RELATION_TYPES,
                )
            },
            **{
                ExtractionOptions.KEY
                + "."
                + key: ImplementationParameters.POLICY
                + "."
                + ExtractionOptions.KEY
                + "."
                + key
                for key in (ExtractionOptions.Field.REPAIR_ATTEMPTS, ExtractionOptions.Field.FAILURE_POLICY)
            },
        },
    )

    @classmethod
    def bind_values(cls, component, values):
        for name in cls.BOUND_FIELDS:
            setattr(
                component,
                name,
                deepcopy(values.get(name, {} if name.endswith(cls.OPTIONS_SUFFIX) else cls.ABSENT)),
            )

    @classmethod
    def initial_options(cls, component, values):
        component.search_options, component.graph_options = values.get(
            SearchOptions.CONFIG_KEY, {}
        ), values.get(cls.Field.GRAPH, {})
        component.extraction_options, component.extraction_prompt = values.get(
            ExtractionOptions.KEY, {}
        ), PromptDefinition.create(())
        component.provider_options = values.get(cls.Field.PROVIDER, {})

    @staticmethod
    def resolve_config(component, project):
        if (
            getattr(component, RAGConfiguration.SOURCE_ATTRIBUTE, RAGConfiguration.ABSENT)
            is not RAGConfiguration.ABSENT
        ):
            return component._configuration_source.resolve_config(project)
        client_values = {}
        runtime = []
        for name, client in (
            (RAGConfiguration.Field.EMBEDDING_PARAMS, component.embedding),
            (RAGConfiguration.Field.EXTRACTION_PARAMS, component.extractor),
            (RAGConfiguration.Field.RERANK_PARAMS, component.reranker),
        ):
            if client is not RAGConfiguration.ABSENT:
                params = getattr(client, ModelClient.PARAMETERS_ATTRIBUTE, {})
                try:
                    component.serialize(params)
                    client_values[name] = params
                except (ValueError, TypeError):
                    runtime.append(name)
        if getattr(component.extractor, ExtractionOptions.KEY, RAGConfiguration.ABSENT):
            client_values[ExtractionOptions.KEY] = deepcopy(component.extractor.extraction)
        layers = [
            (ConfigurationSource.CLIENT, component.parameter_layout.pack(client_values)),
            *component.get_config_layers(project),
        ]
        view = resolve_config(layers, schema=component.describe_config())
        from .embedding import EmbeddingModel

        selected = EmbeddingModel() if component.embedding is RAGConfiguration.ABSENT else component.embedding
        RAGConfigurationView.enforce_embedding(view, selected)
        RAGConfigurationView.set_providers(
            view,
            {
                name: resolve_config(
                    [
                        (
                            ConfigurationSource.CLIENT,
                            getattr(getattr(component, name), RAGConfiguration.CLIENT_PROVIDER_ATTRIBUTE, {}),
                        ),
                        (
                            ConfigurationSource.PROJECT,
                            ImplementationParameters.read(ConfigurationView.read(view).values).policy.get(
                                RAGConfiguration.Field.PROVIDER, {}
                            ),
                        ),
                    ],
                    schema=provider_schema(),
                )
                for name in (
                    RAGConfiguration.Model.EMBEDDING,
                    RAGConfiguration.Model.EXTRACTOR,
                    RAGConfiguration.Model.RERANKER,
                )
            },
        )
        RAGConfigurationView.set_runtime(view, runtime)
        return view

    @staticmethod
    def with_config(component, project, *, prompt=ABSENT):
        """저장 설정을 작업별 사본으로 바인딩한다. 등록된 객체와 SDK 함수를 변경하지 않는다."""
        from .embedding import EmbeddingModel
        from .extraction import TripleExtractor
        from .rerank import RerankModel

        view = component.resolve_config(project)
        values = component.parameter_layout.unpack(ConfigurationView.read(view).values)
        worker = copy(component)
        worker._configuration_source = component
        RAGConfiguration.bind_values(worker, values)
        worker.search_options, worker.graph_options = (
            values.get(SearchOptions.CONFIG_KEY, {}),
            values.get(RAGConfiguration.Field.GRAPH, {}),
        )
        worker.extraction_options = deepcopy(values.get(ExtractionOptions.KEY, {}))
        worker.provider_options = values.get(RAGConfiguration.Field.PROVIDER, {})
        worker._cache_namespace = project.id
        worker._extraction_error = (
            ValueError(RAGConfiguration.MISSING_PROMPT)
            if worker.extraction_options.get(ExtractionOptions.Field.PROMPT_ID) is not RAGConfiguration.ABSENT
            and prompt is RAGConfiguration.ABSENT
            else RAGConfiguration.ABSENT
        )
        worker.extraction_prompt = deepcopy(
            PromptDefinition.create(()) if prompt is RAGConfiguration.ABSENT else prompt
        )
        for name, key, factory in (
            (RAGConfiguration.Model.EMBEDDING, RAGConfiguration.Field.EMBEDDING_PARAMS, EmbeddingModel),
            (RAGConfiguration.Model.EXTRACTOR, RAGConfiguration.Field.EXTRACTION_PARAMS, TripleExtractor),
            (RAGConfiguration.Model.RERANKER, RAGConfiguration.Field.RERANK_PARAMS, RerankModel),
        ):
            client, params = (getattr(component, name), values.get(key, {}))
            if client is RAGConfiguration.ABSENT and params:
                client = factory(**params)
            elif client is not RAGConfiguration.ABSENT and params:
                configure = getattr(client, ModelClient.with_config.__name__, RAGConfiguration.ABSENT)
                if configure is not RAGConfiguration.ABSENT:
                    client = configure(params)
                elif component._options(project).get(key):
                    raise ValueError(RAGConfiguration.CUSTOM_PARAMETERS.format(name=name))
            if client is not RAGConfiguration.ABSENT and callable(
                getattr(client, ModelClient.with_provider.__name__, RAGConfiguration.ABSENT)
            ):
                client = client.with_provider(
                    ConfigurationView.read(RAGConfigurationView(view).provider(name)).values
                )
            setattr(worker, name, client)
        bind_extraction = getattr(
            worker.extractor, RAGConfiguration.CLIENT_EXTRACTION_METHOD, RAGConfiguration.ABSENT
        )
        if bind_extraction is not RAGConfiguration.ABSENT:
            worker.extraction_options = ProjectConfig.merge(
                getattr(worker.extractor, ExtractionOptions.KEY, {}), worker.extraction_options
            )
            if (
                prompt is RAGConfiguration.ABSENT
                and getattr(
                    worker.extractor, RAGConfiguration.CLIENT_PROMPT_ATTRIBUTE, RAGConfiguration.ABSENT
                )
                is not RAGConfiguration.ABSENT
            ):
                worker.extraction_prompt = deepcopy(worker.extractor.prompt)
            worker.extractor = bind_extraction(worker.extraction_options, worker.extraction_prompt)
        elif worker.extractor is not RAGConfiguration.ABSENT and worker.extraction_options.keys() - {
            ExtractionOptions.Field.FAILURE_POLICY
        }:
            raise ValueError(RAGConfiguration.CUSTOM_EXTRACTION)
        worker._configuration_version = RAGConfigurationVersion.create(
            values,
            RAGConfigurationView(view).providers,
            worker.extraction_prompt,
            worker._extraction_error is RAGConfiguration.ABSENT,
        )
        return worker

    @staticmethod
    def describe_config(component):
        from llm.core.schema import object_schema, open_schema, field
        from .search import LexicalCache
        from .embedding import EmbeddingModel
        from .extraction import TripleExtractor
        from .rerank import RerankModel

        def child_schema(client, factory):
            selected = factory() if client is RAGConfiguration.ABSENT else client
            describe = getattr(selected, ModelClient.describe_config.__name__, RAGConfiguration.ABSENT)
            return (
                describe()
                if describe
                else open_schema(
                    type(selected).__qualname__ + RAGConfiguration.CUSTOM_SCHEMA_SUFFIX,
                    category=OpenSchemaKind.IMPLEMENTATION,
                )
            )

        embedding_schema = child_schema(component.embedding, EmbeddingModel)
        return component.parameter_layout.schema(
            object_schema(
                {
                    RAGConfiguration.Field.CHUNK_SIZE: field(
                        SchemaType.INTEGER, minimum=RAGConfiguration.CHUNK_SIZE_MINIMUM
                    ),
                    RAGConfiguration.Field.EMBEDDING_CONCURRENCY: field(
                        SchemaType.INTEGER, minimum=RAGConfiguration.CONCURRENCY_MINIMUM
                    ),
                    RAGConfiguration.Field.EMBEDDING_CACHE_MAX_BYTES: field(
                        SchemaType.INTEGER, minimum=RAGConfiguration.CACHE_BYTES_MINIMUM
                    ),
                    RAGConfiguration.Field.QUERY_CACHE_MAX_BYTES: field(
                        SchemaType.INTEGER,
                        RAGConfiguration.QUERY_CACHE_DESCRIPTION,
                        minimum=RAGConfiguration.CACHE_BYTES_MINIMUM,
                    ),
                    RAGConfiguration.Field.SNAPSHOT_CACHE_MAX_BYTES: field(
                        SchemaType.INTEGER,
                        RAGConfiguration.SNAPSHOT_CACHE_DESCRIPTION,
                        minimum=RAGConfiguration.CACHE_BYTES_MINIMUM,
                    ),
                    RAGConfiguration.Field.EXTRACTION_BATCH_SIZE: field(
                        SchemaType.INTEGER, minimum=RAGConfiguration.EXTRACTION_BATCH_MINIMUM
                    ),
                    RAGConfiguration.Field.SEARCH_CACHE_CHARS: LexicalCache.capacity_schema(),
                    RAGConfiguration.Field.INDEX_BATCH_SIZE: field(
                        SchemaType.INTEGER, minimum=RAGConfiguration.INDEX_BATCH_MINIMUM
                    ),
                    SearchOptions.CONFIG_KEY: search_schema(),
                    RAGConfiguration.Field.GRAPH: object_schema(
                        {
                            RAGConfiguration.Field.BUFFER_POOL_SIZE: field(
                                SchemaType.INTEGER, minimum=RAGConfiguration.GRAPH_BUFFER_MINIMUM
                            ),
                            RAGConfiguration.Field.MAX_NUM_THREADS: field(
                                SchemaType.INTEGER, minimum=RAGConfiguration.GRAPH_THREADS_MINIMUM
                            ),
                        }
                    ),
                    RAGConfiguration.Field.DOCUMENT_KWARGS: deepcopy(embedding_schema),
                    RAGConfiguration.Field.QUERY_KWARGS: deepcopy(embedding_schema),
                    RAGConfiguration.Field.EMBEDDING_PARAMS: embedding_schema,
                    RAGConfiguration.Field.EXTRACTION_PARAMS: child_schema(
                        component.extractor, TripleExtractor
                    ),
                    ExtractionOptions.KEY: extraction_schema(),
                    RAGConfiguration.Field.PROVIDER: provider_schema(),
                    RAGConfiguration.Field.RERANK_PARAMS: child_schema(component.reranker, RerankModel),
                    JobAdmission.KEY: JobAdmission.schema(),
                },
                **{
                    SchemaExtension.RUNTIME_CONFIGURATION: [
                        RAGConfiguration.Model.EMBEDDING,
                        RAGConfiguration.Model.EXTRACTOR,
                        RAGConfiguration.Model.RERANKER,
                        RAGConfiguration.Model.EMBEDDING_ID,
                    ]
                },
            )
        )

    @staticmethod
    def validate_config(component, data):
        if any((key in data for key in RAGConfiguration.RETIRED_BATCH_FIELDS)):
            raise ValueError(RAGConfiguration.RETIRED_BATCH)
        from jsonschema import Draft202012Validator

        error = next(
            Draft202012Validator(component.describe_config()).iter_errors(data), RAGConfiguration.ABSENT
        )
        if error:
            raise ValueError(RAGConfiguration.INVALID + error.message)
        data = component.parameter_layout.unpack(data)
        for value in (data.get(JobAdmission.KEY, {}).get(JobAdmission.MAX_ACTIVE),):
            if value is not RAGConfiguration.ABSENT and type(value) is not int:
                raise ValueError(JobAdmission.INTEGER_REQUIRED)
        for name in (
            RAGConfiguration.Field.CHUNK_SIZE,
            RAGConfiguration.Field.EMBEDDING_CONCURRENCY,
            RAGConfiguration.Field.EMBEDDING_CACHE_MAX_BYTES,
            RAGConfiguration.Field.QUERY_CACHE_MAX_BYTES,
            RAGConfiguration.Field.SNAPSHOT_CACHE_MAX_BYTES,
            RAGConfiguration.Field.EXTRACTION_BATCH_SIZE,
            RAGConfiguration.Field.SEARCH_CACHE_CHARS,
            RAGConfiguration.Field.INDEX_BATCH_SIZE,
        ):
            if name in data and type(data[name]) is not int:
                raise ValueError(RAGConfiguration.INTEGER_REQUIRED.format(name=name))
        from .embedding import EmbeddingModel
        from .extraction import TripleExtractor
        from .rerank import RerankModel

        for client, factory, key in (
            (component.embedding, EmbeddingModel, RAGConfiguration.Field.EMBEDDING_PARAMS),
            (component.extractor, TripleExtractor, RAGConfiguration.Field.EXTRACTION_PARAMS),
            (component.reranker, RerankModel, RAGConfiguration.Field.RERANK_PARAMS),
        ):
            selected = factory() if client is RAGConfiguration.ABSENT else client
            validate = getattr(selected, ModelClient.validate_config.__name__, RAGConfiguration.ABSENT)
            if validate is not RAGConfiguration.ABSENT and key in data:
                validate(deepcopy(data[key]))


class RAGConfigurationView:
    ENFORCED = "enforced"
    MODEL_PROVIDERS = "model_providers"

    def __init__(self, value):
        self._value = value

    @property
    def providers(self):
        return self._value[self.MODEL_PROVIDERS]

    def provider(self, name):
        return self._value[self.MODEL_PROVIDERS][name]

    @classmethod
    def enforce_embedding(cls, view, selected):
        view[cls.ENFORCED] = {
            ImplementationParameters.CONFIG: {
                RAGConfiguration.Field.EMBEDDING_PARAMS: deepcopy(
                    getattr(selected, RAGConfiguration.CLIENT_ENFORCED_ATTRIBUTE, {})
                )
            }
        }

    @classmethod
    def set_providers(cls, view, providers):
        view[cls.MODEL_PROVIDERS] = providers

    @staticmethod
    def set_runtime(view, runtime):
        view[RuntimeConfiguration.runtime.__name__] = runtime


class RAGConfigurationVersion:
    CONFIGURATION = "configuration"
    PROMPT = "prompt"
    PROMPT_RESOLVED = "prompt_resolved"

    @classmethod
    def create(cls, values, providers, prompt, resolved):
        return revision_token(
            {
                cls.CONFIGURATION: values,
                RAGConfigurationView.MODEL_PROVIDERS: {
                    k: ConfigurationView.read(v).values for k, v in providers.items()
                },
                cls.PROMPT: prompt,
                cls.PROMPT_RESOLVED: resolved,
            }
        )
