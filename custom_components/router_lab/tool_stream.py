"""Keep opaque tool signatures and serialize parallel deltas for HA's tool reader.

Only complete tool arguments are buffered. Text still streams immediately. The
bounded, client-local cache is never persisted or shared with another endpoint.
"""
from collections import OrderedDict
from copy import copy, deepcopy
import json


class ToolMetadata:
    def __init__(self):self.items=OrderedDict()

    def remember(self,scope,model,calls):
        for call in calls:
            extra=getattr(call,'extra_content',None)
            if not extra:continue
            if len(json.dumps(extra))>131072:raise ValueError('Tool signature exceeds supported size')
            key=(scope,model,call.id)
            self.items[key]=deepcopy(extra);self.items.move_to_end(key)
        while len(self.items)>64:self.items.popitem(last=False)

    def restore(self,scope,model,messages):
        result=deepcopy(messages)
        for message in result:
            if message.get('role')!='assistant':continue
            for call in message.get('tool_calls',[]):
                extra=self.items.get((scope,model,call.get('id')))
                if extra:call['extra_content']=deepcopy(extra)
        return result


class ToolStream:
    def __init__(self,stream,cache,scope,model):
        self.original=stream;self.cache=cache;self.scope=scope;self.model=model
    def __getattr__(self,name):return getattr(self.original,name)
    async def __aenter__(self):
        await self.original.__aenter__();return self
    async def __aexit__(self,*args):return await self.original.__aexit__(*args)
    def __aiter__(self):return self._iterate()

    async def _iterate(self):
        calls={};finished=False;size=0
        async for chunk in self.original:
            if not chunk.choices:
                yield chunk;continue
            choice=chunk.choices[0];delta=choice.delta
            for partial in delta.tool_calls or []:
                index=getattr(partial,'index',None) or 0
                if index not in calls:
                    if len(calls)>=32:raise ValueError('Too many tool calls')
                    calls[index]=copy(partial);calls[index].function=copy(partial.function)
                else:
                    current=calls[index]
                    if partial.id:current.id=partial.id
                    if partial.function:
                        if current.function is None:current.function=copy(partial.function)
                        else:
                            if partial.function.name:current.function.name=partial.function.name
                            current.function.arguments=(current.function.arguments or '')+(partial.function.arguments or '')
                    if getattr(partial,'extra_content',None):current.extra_content=deepcopy(partial.extra_content)
                size+=len(getattr(partial.function,'arguments','') or '')
                if size>262144:raise ValueError('Tool arguments exceed supported size')
            if choice.finish_reason:
                ordered=[calls[k] for k in sorted(calls)]
                if any(not c.id or not c.function or not c.function.name for c in ordered):raise ValueError('Incomplete tool call')
                self.cache.remember(self.scope,self.model,ordered)
                for call in ordered:
                    out=copy(chunk);out.choices=[copy(choice)];out.choices[0].finish_reason=None
                    out.choices[0].delta=copy(delta);out.choices[0].delta.content=None
                    out.choices[0].delta.tool_calls=[call];out.choices[0].delta.role='assistant'
                    yield out
                finished=True
            # Suppress original parallel fragments, preserving ordinary text.
            if delta.tool_calls:
                out=copy(chunk);out.choices=[copy(choice)];out.choices[0].delta=copy(delta)
                out.choices[0].delta.tool_calls=None
                yield out
            else:yield chunk
        if calls and not finished:raise ValueError('Tool stream ended before completion')
