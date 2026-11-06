// Electron 只负责标准 exe / DLL 目录与 ZIP；安装/卸载交给锁定的 Inno Setup。
if (require.main === module) {
  const { build, Platform, Arch } = require('electron-builder');
  const config = {};
  const distribution = process.argv.find(argument=>argument.startsWith('--config.electronDist='));
  if (distribution) config.electronDist = distribution.slice('--config.electronDist='.length);
  build({ targets: Platform.WINDOWS.createTarget(['zip'],Arch.x64), config, publish:'never' })
    .catch(error=>{console.error(error);process.exitCode=1;});
}
