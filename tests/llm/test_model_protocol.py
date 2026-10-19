"""Common values preserve meaning without an SDK wire shape."""
from copy import deepcopy
import json
import unittest

from llm.core.model_protocol import (
    ContentPart, ModelMessage, ModelRequest, ModelResponse, StopReason,
    ToolCall, ToolDefinition, ToolResult, option_copy,
)
from llm.providers.model_codec import (
    definition_from_legacy, definition_to_legacy, message_from_legacy,
    message_to_legacy, request_from_legacy, request_to_legacy,
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

    def test_legacy_signatures_arguments_and_multimodal_round_trip(self):
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
                self.assertEqual(message_to_legacy(message_from_legacy(value)),value)

    def test_worker_and_graph_v1_definition_encoding_is_stable(self):
        definition={'type':'function','function':{'name':'lookup','description':'Lookup',
                    'parameters':{'type':'object'},'strict':True,'vendor':'preserve'}}
        self.assertEqual(definition_to_legacy(definition_from_legacy(definition)),definition)

    def test_request_codec_preserves_client_and_does_not_mutate_input(self):
        client=object()
        request={'messages':[{'role':'user','content':'task'}],'model':'test','client':client,
                 'tools':[definition_to_legacy(self.request().tools[0])]}
        common=request_from_legacy(request)
        self.assertEqual(request_to_legacy(common),request)
        self.assertIs(common.parameters['client'],client)
        common.messages[0]=common.messages[0].with_text('changed')
        self.assertEqual(request['messages'][0]['content'],'task')

    def test_versions_and_invalid_content_fail_closed(self):
        value=self.request().to_dict()
        value['version']=2
        with self.assertRaises(ValueError):
            ModelRequest.from_dict(value)
        with self.assertRaises(TypeError):
            ModelMessage('user',[{'type':'text','text':'wire'}])
        with self.assertRaises(ValueError):
            ModelMessage('tool','missing reference')
