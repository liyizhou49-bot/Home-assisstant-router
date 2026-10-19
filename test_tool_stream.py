import importlib.util
from pathlib import Path
from types import SimpleNamespace as N
import unittest

spec=importlib.util.spec_from_file_location('stream_tested',Path(__file__).parent/'custom_components/router_lab/tool_stream.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def call(index,id=None,name=None,args='',extra=None):
    return N(index=index,id=id,function=N(name=name,arguments=args),extra_content=extra)

def chunk(calls=None,text=None,finish=None):
    return N(choices=[N(delta=N(tool_calls=calls,content=text,role='assistant'),finish_reason=finish)],usage=None)

class Stream:
    def __init__(self,chunks):self.chunks=chunks;self.closed=False
    def __aiter__(self):return self.iterate()
    async def iterate(self):
        for value in self.chunks:yield value
    async def __aenter__(self):return self
    async def __aexit__(self,*args):self.closed=True

class Checks(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_fragments_not_lost_and_signature_restored(self):
        original=Stream([chunk([call(0,'a','one','{',{'google':{'thought_signature':'opaque'}}),call(1,'b','two','{')]),
            chunk([call(1,args='"x":2}'),call(0,args='"x":1}')]),chunk(finish='tool_calls')])
        cache=module.ToolMetadata()
        async with module.ToolStream(original,cache,'user1','gemini-test') as stream:result=[c async for c in stream]
        emitted=[v for c in result for v in (c.choices[0].delta.tool_calls or [])]
        self.assertEqual([(c.id,c.function.arguments) for c in emitted],[('a','{"x":1}'),('b','{"x":2}')])
        history=[{'role':'assistant','tool_calls':[{'id':'a'},{'id':'b'}]}]
        restored=cache.restore('user1','gemini-test',history)
        self.assertEqual(restored[0]['tool_calls'][0]['extra_content'],{'google':{'thought_signature':'opaque'}})
        self.assertNotIn('extra_content',history[0]['tool_calls'][0]);self.assertTrue(original.closed)
        self.assertEqual(cache.restore('other-user','gemini-test',history),history)
        self.assertEqual(cache.restore('user1','other-model',history),history)

    async def test_text_still_arrives_before_tool_completion(self):
        source=Stream([chunk(text='请稍等'),chunk([call(0,'a','one','{}')]),chunk(finish='tool_calls')])
        stream=module.ToolStream(source,module.ToolMetadata(),'','')
        it=stream.__aiter__();self.assertEqual((await anext(it)).choices[0].delta.content,'请稍等')
        await it.aclose()

    async def test_truncated_stream_rejected_before_execution(self):
        source=Stream([chunk([call(0,'a','one','{"x":')])])
        with self.assertRaises(ValueError):[c async for c in module.ToolStream(source,module.ToolMetadata(),'','')]

    def test_cache_is_bounded(self):
        cache=module.ToolMetadata()
        for i in range(70):cache.remember('s','m',[call(0,str(i),'one','{}',{'google':{'thought_signature':'opaque'}})])
        self.assertEqual(len(cache.items),64)

if __name__=='__main__':unittest.main()
