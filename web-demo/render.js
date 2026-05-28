// meet-transcribe web demo —— 实时转写消息渲染纯函数。
//
// 设计要点：
//   - 不接 DOM，只做 string in / string out，便于浏览器内黑盒测试。
//   - escHtml 兜底任何字符串字段，防止上游文本含 < / & 时的 XSS。
//   - speakerBadge 返回 4 种形态：
//       speaker === -2          → 淡灰 [silence]
//       speaker_resolved.name   → 绿色徽章 + 显示名 + 分数
//       speaker  > 0  无 resolved → 灰底 "S<n>"
//       其它（-1 / 缺省）         → 空串
//
// 暴露在 window.MTRender，index.html 与 test.html 共用同一份。

(function (global) {
  "use strict";

  function escHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[c];
    });
  }

  function speakerBadge(line) {
    if (!line || typeof line !== "object") return "";
    var spk = line.speaker;
    if (spk === -2) return '<span class="speaker silence">[silence]</span>';
    var r = line.speaker_resolved;
    if (r && r.name) {
      var score = typeof r.score === "number" ? " " + r.score.toFixed(2) : "";
      return (
        '<span class="speaker resolved" title="speaker_id=' +
        escHtml(r.id) +
        '">' +
        escHtml(r.name) +
        '<span class="resolved-score">' +
        score +
        "</span></span>"
      );
    }
    if (typeof spk === "number" && spk > 0) {
      return '<span class="speaker unresolved s-' + spk + '">S' + spk + "</span>";
    }
    return "";
  }

  function renderLines(lines, finalCls) {
    if (!Array.isArray(lines) || !lines.length) return "";
    return lines
      .filter(function (ln) {
        return ln && (ln.text || ln.speaker > 0 || ln.speaker === -2);
      })
      .map(function (ln) {
        var badge = speakerBadge(ln);
        var text = ln.speaker === -2 ? "" : escHtml(ln.text || "");
        return '<div class="line ' + finalCls + '">' + badge + text + "</div>";
      })
      .join("");
  }

  global.MTRender = {
    escHtml: escHtml,
    speakerBadge: speakerBadge,
    renderLines: renderLines,
  };
})(typeof window !== "undefined" ? window : globalThis);
