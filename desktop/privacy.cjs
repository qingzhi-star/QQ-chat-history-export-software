const {createHash}=require('node:crypto');
const fs=require('node:fs');

class PrivacyGate {
  constructor(metadata,source){
    if(createHash('sha256').update(source.replace(/\r\n/g,'\n')).digest('hex')!==metadata.sha256)throw new Error('隐私条款校验失败，请重新安装完整官方版本');
    this.metadata=Object.freeze({...metadata});this.accepted=false;this.avatars=false;
  }
  info(){return this.metadata;}
  accept(value){
    if(!value || value.version!==this.metadata.version || value.sha256!==this.metadata.sha256
      || value.sensitive!==true || typeof value.avatars!=='boolean')throw new Error('请阅读并确认本次隐私条款及敏感信息处理说明');
    this.accepted=true;this.avatars=value.avatars;
  }
  authorize(){if(!this.accepted)throw new Error('请先阅读并同意隐私条款');}
  static load(root,sourcePath){
    const path=require('node:path');
    return new PrivacyGate(JSON.parse(fs.readFileSync(path.join(root,'privacy-policy.json'),'utf8')),
      fs.readFileSync(sourcePath||path.join(root,'privacy.md'),'utf8'));
  }
}
module.exports={PrivacyGate};
