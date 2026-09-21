import { Component, createRef } from "react";

export class AppErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
    this.reloadButtonRef = createRef();
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("应用界面渲染失败", error, info);
  }

  componentDidUpdate(_previousProps, previousState) {
    if (!previousState.error && this.state.error) {
      this.reloadButtonRef.current?.focus();
    }
  }

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <main className="app-error-boundary" aria-labelledby="app-error-boundary-title">
        <section role="alert" aria-live="assertive">
          <span className="app-error-boundary-kicker">页面恢复</span>
          <h1 id="app-error-boundary-title">页面暂时无法显示</h1>
          <p>界面组件遇到异常，当前内容没有正常呈现。请重新加载后再试。</p>
          <button
            ref={this.reloadButtonRef}
            type="button"
            onClick={() => globalThis.location?.reload?.()}
          >
            重新加载页面
          </button>
          <small>重新加载会保留当前地址与筛选条件；未保存的输入可能丢失。</small>
        </section>
      </main>
    );
  }
}
