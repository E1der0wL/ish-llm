"""Common values preserve meaning without an SDK wire shape."""
from copy import deepcopy
import json
import unittest

from llm.core.model_protocol import (
    ContentPart, ModelMessage, ModelRequest, ModelResponse, StopReason,
    ToolCall, ToolDefinition, ToolResult, option_copy,
)
from llm.providers.wire_codec import (
    decode_tool_definition, encode_tool_definition, decode_message,
    encode_message, decode_request, encode_request,
)


class ModelProtocolTests(unittest.TestCase):
    def request(self):
        return ModelRequest([
            ModelMessage('user',(ContentPart('text','question'),ContentPart('image','data:image/png;base64,YQ==','image/png'))),
            ModelMessage('assistant',calls=(ToolCall('a','lookup',{'q':'value'}),),opaque={'other':{'signature':'signed'}}),
            ModelMessage('tool',result=ToolResult('a','answer','lookup')),
        ], {'model':'custom'}, (ToolDefinition('lookup','Lookup',{'type':'object'},True),))

    def test_json_round_trip_and_exchange_matching(self):
        request=self.request()
        request.validate_exchanges()
        restored=ModelRequest.from_dict(json.loads(json.dumps(request.to_dict())))
        self.assertEqual(request,restored)
        self.assertEqual(restored.messages[1].opaque,{'other':{'signature':'signed'}})
        response=ModelResponse(ModelMessage('assistant','answer'),StopReason.COMPLETE,{'total_tokens':3})
        self.assertEqual(ModelResponse.from_dict(json.loads(json.dumps(response.to_dict()))),response)

    def test_missing_duplicate_and_unmatched_results_fail(self):
        for messages in (self.request().messages[:-1], self.request().messages+[self.request().messages[-1]],
                         [ModelMessage('tool',result=ToolResult('absent','value'))]):
            with self.subTest(messages=messages), self.assertRaises(ValueError):
                ModelRequest(messages).validate_exchanges()

    def test_fragmented_arguments_are_not_completed_calls(self):
        for arguments in ('{"q":', [], {'bad':float('nan')}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                ToolCall('a','lookup',arguments)

    def test_no_usage_is_unknown_and_counts_are_checked(self):
        self.assertIsNone(ModelResponse(ModelMessage('assistant','')).usage)
        for usage in ({'total_tokens':False},{'total_tokens':-1},{'details':{'x':0.5}}):
            with self.subTest(usage=usage), self.assertRaises(ValueError):
                ModelResponse(ModelMessage('assistant',''),usage=usage)

    def test_copy_preserves_runtime_client_but_detaches_options_and_opaque(self):
        client=object()
        request=self.request()
        request.parameters.update(client=client,nested={'values':[]})
        copied=option_copy(request)
        self.assertIs(copied.parameters['client'],client)
        copied.parameters['nested']['values'].append(1)
        copied.messages[1].opaque['other']['signature']='changed'
        self.assertEqual(request.parameters['nested']['values'],[])
        self.assertEqual(request.messages[1].opaque['other']['signature'],'signed')
        with self.assertRaises(ValueError):
            request.to_dict()

    def test_sdk_signatures_arguments_and_multimodal_round_trip(self):
        values=[
            {'role':'assistant','content':None,'reasoning_content':'thinking',
             'provider_specific_fields':{'signature':'signed'},'tool_calls':[
                {'id':'signed-call','type':'function','extra':'opaque','function':{
                    'name':'lookup','arguments':'{ "q" : "value" }','signature':'block'}}]},
            {'role':'tool','tool_call_id':'signed-call','name':'lookup','content':'result'},
            {'role':'user','content':[{'type':'text','text':'question'},
                {'type':'image_url','image_url':{'url':'https://example.test/image','detail':'high'}}]},
        ]
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(encode_message(decode_message(value)),value)

    def test_sdk_tool_definition_preserves_wire_extensions(self):
        definition={'type':'function','function':{'name':'lookup','description':'Lookup',
                    'parameters':{'type':'object'},'strict':True,'vendor':'preserve'}}
        self.assertEqual(encode_tool_definition(decode_tool_definition(definition)),definition)

    def test_request_codec_preserves_client_and_does_not_mutate_input(self):
        client=object()
        request={'messages':[{'role':'user','content':'task'}],'model':'test','client':client,
                 'tools':[encode_tool_definition(self.request().tools[0])]}
        common=decode_request(request)
        self.assertEqual(encode_request(common),request)
        self.assertIs(common.parameters['client'],client)
        common.messages[0]=common.messages[0].with_text('changed')
        self.assertEqual(request['messages'][0]['content'],'task')
        request['tool_choice'] = {'type': 'function', 'function': {'name': 'act'}}
        self.assertEqual(encode_request(decode_request(request)), request)

    def test_versions_and_invalid_content_fail_closed(self):
        value=self.request().to_dict()
        value['version']=2
        with self.assertRaises(ValueError):
            ModelRequest.from_dict(value)
        with self.assertRaises(TypeError):
            ModelMessage('user',[{'type':'text','text':'wire'}])
        with self.assertRaises(ValueError):
            ModelMessage('tool','missing reference')

    def test_single_current_record_shape_and_owned_fields_are_required(self):
        for value, decode in ((self.request().to_dict(), ModelRequest.from_dict),
                              (ModelMessage('user', 'input').to_dict(), ModelMessage.from_dict),
                              (ModelResponse(ModelMessage('assistant', 'answer')).to_dict(), ModelResponse.from_dict)):
            for corrupted in ({**value, 'version': True}, {**value, 'extra': None},
                              {key: item for key, item in value.items() if key != 'version'}):
                with self.subTest(decode=decode.__qualname__, value=corrupted), self.assertRaises(ValueError):
                    decode(corrupted)
            self.assertEqual(decode(value).to_dict(), value)
        for field in ('messages', 'tools'):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'typed request fields'):
                ModelRequest(parameters={field: []})

    def test_typed_processor_edits_preserve_opaque_and_reject_dict_projection(self):
        from dataclasses import replace
        from llm.components.processing import CompletionMessage
        item = CompletionMessage(ModelMessage('assistant', 'before',
            (ToolCall('a', 'lookup', {'q': 'original'}, {'fake': {'signature': 'call'}}),),
            opaque={'fake': {'signature': 'message'}}))
        edited = item.with_text('after')
        edited = replace(edited, message=replace(edited.message,
            calls=(replace(edited.message.calls[0], arguments={'q': 'changed'}),)))
        self.assertEqual(edited.message.text, 'after')
        self.assertEqual(edited.message.calls[0].arguments, {'q': 'changed'})
        self.assertEqual(edited.message.opaque['fake'], {'signature': 'message'})
        self.assertEqual(edited.message.calls[0].opaque['fake'], {'signature': 'call'})
        self.assertEqual(item.message.calls[0].arguments, {'q': 'original'})
        self.assertEqual(item.message.text, 'before')
        with self.assertRaises(ValueError):
            replace(edited.message, calls=edited.message.calls * 2)
        self.assertEqual(len(edited.message.calls), 1)
        self.assertFalse(hasattr(item, 'value'))
        with self.assertRaises(TypeError):
            CompletionMessage({'role': 'user', 'content': 'old API'})

    def test_streamed_opaque_metadata_and_content_edits_survive_adapter_round_trip(self):
        from dataclasses import replace
        from llm.providers.litellm import LiteLLMCompletionDecoder
        decoder = LiteLLMCompletionDecoder()
        for delta, finish in (({'provider_specific_fields': {'signature': 'signed'}, 'reasoning_content': 'think'}, None),
                              ({'provider_specific_fields': {'cache': 'hint'}, 'content': 'answer'}, 'stop')):
            list(decoder.decode({'choices': [{'delta': delta, 'finish_reason': finish}]}))
        response = decoder.finish()
        self.assertEqual(response.message.opaque['litellm']['provider_specific_fields'],
                         {'signature': 'signed', 'cache': 'hint'})
        self.assertEqual(response.message.opaque['litellm']['reasoning_content'], 'think')
        message = decode_message({'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': 'old', 'detail': 'high'}}]})
        message = replace(message, content=(replace(message.content[0], value='new'),))
        self.assertEqual(encode_message(message)['content'][0]['image_url'], {'url': 'new', 'detail': 'high'})

    def test_typed_copies_and_multimodal_edits_preserve_detached_state(self):
        from dataclasses import replace
        from llm.components.processing import CompletionMessage
        item = CompletionMessage(ModelMessage('user', (ContentPart('image', 'old',
            opaque={'fake': {'signature': 'image'}}),)))
        edited = replace(item, message=replace(item.message,
            content=(replace(item.message.content[0], value='new'),)))
        self.assertEqual(item.message.content[0].value, 'old')
        self.assertEqual(edited.message.content[0].value, 'new')
        self.assertEqual(edited.message.content[0].opaque['fake']['signature'], 'image')
        copied = deepcopy(edited)
        copied.message.content[0].opaque['fake']['signature'] = 'copy'
        self.assertEqual(edited.message.content[0].opaque['fake']['signature'], 'image')
        self.assertEqual(copied.message.content[0].opaque['fake']['signature'], 'copy')

    def test_unmarked_typed_stream_override_preserves_state_and_rejects_dict(self):
        import asyncio
        from llm.engines.base import BaseEngine
        from tests.llm.support.common_provider import CommonProvider
        request = self.request()
        client = object()
        request.parameters['client'] = client
        provider = CommonProvider([{'sealed': {'say': 'answer'}}])
        class Forwarding(BaseEngine):
            async def stream_completion(self, request, **options):
                request = option_copy(request)
                request.parameters['timeout'] = 2
                async for item in super().stream_completion(request, **options):
                    yield item
        async def exercise():
            capture = []
            engine = Forwarding(completion_adapter=provider)
            async for _ in engine.stream_completion(request, response=capture):
                pass
            self.assertEqual(capture[0].message.text, 'answer')
            sent = provider.requests[0]
            self.assertEqual(sent.messages, request.messages)
            self.assertIs(sent.parameters['client'], client)
            self.assertEqual(sent.parameters['timeout'], 2)
            self.assertNotIn('timeout', request.parameters)
            with self.assertRaisesRegex(TypeError, 'ModelRequest'):
                async for _ in BaseEngine(completion_adapter=provider).stream_completion({}):
                    pass
            self.assertEqual(len(provider.requests), 1)
        asyncio.run(exercise())
