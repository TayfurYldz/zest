export const initialState = {
  activeView: "mission",
  dashboardSnapshot: null,
  selectedRunId: "",
  selectedAnalysis: null,
  analysisLoading: false,
  analysisError: "",
  dashboardLoading: false,
  dashboardError: "",
  transportState: "OFFLINE",
  lastEventId: "",
  inspector: null,
};

export function createStore() {
  let state = { ...initialState };
  const listeners = new Set();
  return {
    getState: () => state,
    update(patch, { notify = true } = {}) { state = { ...state, ...patch }; if (notify) listeners.forEach((listener) => listener(state)); },
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
  };
}
