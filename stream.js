(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AXStream = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  function requestId(cryptoProvider = globalThis.crypto) {
    if (typeof cryptoProvider?.randomUUID === 'function') return cryptoProvider.randomUUID();
    if (typeof cryptoProvider?.getRandomValues !== 'function') throw new Error('안전한 요청 ID를 만들 수 없습니다. 최신 브라우저를 사용하세요.');
    const bytes = cryptoProvider.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  }
  function validateBytes(text) {
    if (typeof text !== 'string' || !text.trim()) throw new Error('입력할 문구가 없습니다.');
    if (new TextEncoder().encode(text).byteLength > 1024 * 1024) throw new Error('입력 내용은 UTF-8 1 MiB 이내로 입력하세요. 원문은 유지됩니다.');
    return text;
  }
  function validateTable(rows, {header = false} = {}) {
    if (rows.length > 2000) throw new Error('표는 2000행 이내로 붙여넣으세요.');
    if (rows.some(row => row.length > 30)) throw new Error('표는 30열 이내로 붙여넣으세요.');
    if (rows.reduce((sum, row) => sum + row.length, 0) > 20000) throw new Error('표는 20000셀 이내로 붙여넣으세요.');
    for (let i = 0; i < rows.length; i++) {
      for (const value of rows[i]) {
        const limit = header && i === 0 ? 100 : 1000;
        if (Array.from(value).length > limit) throw new Error(`${i + 1}행: ${header && i === 0 ? '열 이름' : '셀'}은 ${limit}자 이내로 입력하세요. 원문은 유지됩니다.`);
      }
    }
    return rows;
  }
  function errorMessage(event) {
    const status = event.upstreamStatus ?? event.status;
    const retryAfter = event.retryAfter ?? event.retry_after;
    return [event.message || '답변을 완료하지 못했습니다.', event.code, event.stage,
      status ? `HTTP ${status}` : '', retryAfter != null ? `재시도 안내 ${retryAfter}` : ''].filter(Boolean).join(' · ');
  }
  function sourceUrl(item) {
    const value = item && typeof item === 'object' ? item.source_url ?? item.url ?? item.sourceUrl : null;
    if (typeof value !== 'string') return null;
    try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : null; } catch { return null; }
  }
  async function consume(body, {signal, onDelta = () => {}, onEvidence = () => {}, onSession = () => {}} = {}) {
    if (!body?.getReader) throw new Error('응답 스트림이 없습니다.');
    const reader = body.getReader();
    const decoder = new TextDecoder('utf-8', {fatal: true});
    const state = {text: '', evidence: [], completed: false};
    const seen = new Set();
    let pending = '', total = 0;
    const abort = () => { reader.cancel(signal.reason).catch(() => {}); };
    signal?.addEventListener('abort', abort, {once: true});
    function process(line) {
      if (!line.trim()) return;
      let event;
      try { event = JSON.parse(line); } catch { throw new Error('응답 JSON을 읽지 못했습니다.'); }
      if (!event || typeof event !== 'object' || Array.isArray(event)) throw new Error('응답 JSON 형식을 확인하세요.');
      const id = event.event_id;
      if (id != null && seen.has(id)) return;
      if (id != null) seen.add(id);
      if (event.type === 'delta' && typeof event.text === 'string') { state.text += event.text; onDelta(event.text); }
      if (event.type === 'session' && typeof event.conversationId === 'string') onSession(event.conversationId);
      if (event.type === 'evidence') { state.evidence.push(event.data); onEvidence(event.data); }
      if (event.type === 'error') throw new Error(errorMessage(event));
      if (event.type === 'completed') state.completed = true;
    }
    try {
      while (!state.completed) {
        signal?.throwIfAborted();
        const {value, done} = await reader.read();
        signal?.throwIfAborted();
        if (value) total += value.byteLength;
        if (total > 8 * 1024 * 1024) throw new Error('응답 크기 제한을 초과했습니다.');
        pending += done ? decoder.decode() : decoder.decode(value, {stream: true});
        let boundary;
        while (!state.completed && (boundary = pending.indexOf('\n')) !== -1) {
          const line = pending.slice(0, boundary).replace(/\r$/, '');
          pending = pending.slice(boundary + 1);
          if (new TextEncoder().encode(line).byteLength > 1024 * 1024) throw new Error('응답 줄 크기 제한을 초과했습니다.');
          process(line);
        }
        if (state.completed) break;
        if (new TextEncoder().encode(pending).byteLength > 1024 * 1024) throw new Error('응답 줄 크기 제한을 초과했습니다.');
        if (done) { if (pending.trim()) process(pending); break; }
      }
      if (!state.completed) throw new Error('완료 신호 없이 연결이 종료되었습니다.');
      return state;
    } finally {
      signal?.removeEventListener('abort', abort);
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  }
  return {consume, validateBytes, validateTable, sourceUrl, errorMessage, requestId};
});
