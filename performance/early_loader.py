"""Browser-only switch of Material Skin's deferred-loader registration.

Unlike HTML route.fetch()/route.fulfill(), this runs without intercepting network
responses; on the user's LMS even an unmodified fulfilled response broke assets.
"""

EARLY_LOADER_INIT_SCRIPT = r"""(() => {
  const state = {registrations: 0, firings: 0, registeredAt: null, firedAt: null};
  Object.defineProperty(window, '__materialLoaderExperiment', {value: state});
  const nativeAddEventListener = window.addEventListener;
  window.addEventListener = function(type, listener, options) {
    if (type === 'load' && typeof listener === 'function' && listener.name === 'loadOtherFiles') {
      state.registrations++;
      if (state.registeredAt === null) state.registeredAt = performance.now();
      return nativeAddEventListener.call(this, 'DOMContentLoaded', function(event) {
        state.firings++;
        if (state.firedAt === null) state.firedAt = performance.now();
        return listener.call(this, event);
      }, options);
    }
    return nativeAddEventListener.apply(this, arguments);
  };
})()"""

EARLY_LOADER_STATE_JS = """() => {
  const state = window.__materialLoaderExperiment;
  if (!state) return null;
  return {registrations: state.registrations, firings: state.firings,
          registeredAt: state.registeredAt, firedAt: state.firedAt};
}"""
