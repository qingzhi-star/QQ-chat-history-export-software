const test=require('node:test');
const assert=require('node:assert/strict');
const {createHash}=require('node:crypto');
const {PrivacyGate}=require('../privacy.cjs');
const text='synthetic privacy policy\n';
const metadata={version:'2026-11-06.1',url:'https://qingzhi-star.github.io/QL-qq-chat-history-exporter/privacy.html',sha256:createHash('sha256').update(text).digest('hex')};
test('privacy gate rejects missing, stale or incomplete consent and never starts accepted',()=>{
  const gate=new PrivacyGate(metadata,text);
  assert.throws(()=>gate.authorize(),/隐私条款/);
  for(const value of [null,{}, {...metadata,sensitive:false,avatars:false},{...metadata,version:'old',sensitive:true,avatars:false}]){
    assert.throws(()=>gate.accept(value));assert.equal(gate.accepted,false);
  }
  gate.accept({...metadata,sensitive:true,avatars:false});gate.authorize();assert.equal(gate.avatars,false);
  assert.equal(new PrivacyGate(metadata,text).accepted,false);
});
test('policy content is pinned and avatar network permission is separate',()=>{
  assert.throws(()=>new PrivacyGate(metadata,text+'changed'),/校验失败/);
  const gate=new PrivacyGate(metadata,text.replace(/\n/g,'\r\n'));
  gate.accept({...metadata,sensitive:true,avatars:true});assert.equal(gate.avatars,true);
});
