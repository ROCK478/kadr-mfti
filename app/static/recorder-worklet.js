class TaktRecorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(4096);
    this.length = 0;
    this.port.onmessage = event => {
      if (event.data === 'flush') {
        if (this.length) this.port.postMessage(this.buffer.slice(0, this.length));
        this.length = 0;
        this.port.postMessage('flushed');
      }
    };
  }
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (channel) {
      for (const sample of channel) {
        this.buffer[this.length++] = sample;
        if (this.length === this.buffer.length) {
          this.port.postMessage(this.buffer.slice());
          this.length = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor('takt-recorder', TaktRecorder);
