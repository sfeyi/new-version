"""用浏览器和可控时间验证导拍文字可读时长、方向去抖及重要提示立即显示。"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


CASES = """async () => {
  const {HintPacer} = await import('/static/hints.js');
  const check=(condition,message)=>{if(!condition) throw new Error(message);};
  const hint=(message, options={})=>({who:'摄影者',message,mode:'fine',key:'background',...options});
  const findings={};

  // Rapid left/right estimates must not replace the first readable instruction.
  const noise=new HintPacer();
  check(noise.update(hint('缓慢转动手机'),0)?.message==='缓慢转动手机','Initial guidance was delayed');
  for(let t=100;t<=8000;t+=100) {
    const text=t%200 ? '向左转' : '向右转';
    check(noise.update(hint(text),t)===null,'Alternating directions flickered');
  }
  findings.rapidAlternationSuppressed=true;

  // A sustained, genuinely changed direction should eventually replace the old cue,
  // but never before both readable dwell and stable-candidate periods have elapsed.
  const turn=new HintPacer();
  turn.update(hint('向右转'),0);
  for(const t of [100,400,800,1600,2499])
    check(turn.update(hint('向左转'),t)===null,'A direction replaced text before 2.5 seconds');
  check(turn.update(hint('向左转'),2500)?.message==='向左转','Sustained direction never appeared');
  check(turn.update(hint('向右转'),4999)===null,'A fresh candidate appeared without confirmation');
  check(turn.update(hint('向右转'),5500)===null,'Candidate was accepted in under 650 milliseconds');
  check(turn.update(hint('向右转'),5650)?.message==='向右转','New candidate did not appear after confirmation');
  findings.minimumDisplayMs=2500;
  findings.candidateConfirmationMs=650;

  // Reverting to the displayed hint cancels the old queued direction completely.
  const stale=new HintPacer();
  stale.update(hint('向右转'),0);
  stale.update(hint('向左转'),2600);
  check(stale.update(hint('向右转'),3300)===null,'Returning to current text repainted it');
  check(stale.update(hint('向上仰'),4000)===null,'Cancelled left turn leaked into a newer cue');
  check(stale.update(hint('向上仰'),4600)===null,'New cue inherited cancelled candidate time');
  check(stale.update(hint('向上仰'),4650)?.message==='向上仰','Latest stable cue did not replace old direction');
  findings.staleCandidateDiscarded=true;

  // Moving to another stage must be immediate, even if its text looks identical.
  const stages=new HintPacer();
  stages.update(hint('保持片刻'),0);
  const stage=stages.update(hint('保持片刻',{key:'framing'}),1);
  check(stage?.key==='framing','A new stage was held behind old instructions');
  const failure=stages.update(hint('相机已断开',{key:'framing',mode:'error'}),2);
  check(failure?.mode==='error','Camera failure was delayed');
  const ready=stages.update(hint('可以拍摄',{key:'framing',mode:'ready'}),3);
  check(ready?.mode==='ready','Capture-ready feedback was delayed');
  const immediate=stages.update(hint('正在切换镜头',{key:'framing'}),4,{immediate:true});
  check(immediate?.message==='正在切换镜头','Explicit user action was delayed');
  check(stages.update(hint('正在切换镜头',{key:'framing'}),5,{immediate:true})===null,'Identical urgent text was repainted');
  findings.stageAndUrgentImmediate=true;

  // A reset discards candidates and an emitted object must not mutate internal state.
  const owned=new HintPacer();
  const input=hint('向右转'), before=JSON.stringify(input);
  const emitted=owned.update(input,0);
  check(JSON.stringify(input)===before,'Hint pacing mutated model output');
  emitted.message='外部修改';
  check(owned.update(hint('向右转'),1)===null,'Returned object aliases the internal visible hint');
  owned.update(hint('向左转'),2500);
  owned.reset();
  check(owned.update(hint('新参考照'),2501)?.message==='新参考照','Reset retained a pending old instruction');
  check(owned.update(hint('重新开始'),0)?.message==='重新开始','Rewound clock retained old timing');
  findings.resetAndOwnership=true;
  return findings;
}"""


def main():
    qa = ROOT / "build/qa"
    qa.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT)))
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            cached = list((ROOT.parent / ".browser").glob("chromium_headless_shell-*/**/chrome-headless-shell.exe"))
            executable = os.environ.get("CHROMIUM_PATH") or (str(cached[0]) if cached else None)
            browser = pw.chromium.launch(executable_path=executable)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            findings = page.evaluate(CASES)
            (qa / "hint-pacing-results.json").write_text(json.dumps(findings, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(findings, indent=2))
            print("Readable hint duration, direction stability, stale-candidate cancellation and immediate stage/error updates passed.")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
