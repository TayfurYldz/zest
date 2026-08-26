export const initialState = {
  activeView: "mission",
  dashboardSnapshot: null,
  selectedRunId: "",
  selectedAnalysis: null,
  analysisLoading: false,
  analysisError: "",
  dashboardLoading: false,
  dashboardError: "",
  inspector: null,
};

export function createStore() {
  let state = { ...initialState };
  const listeners = new Set();
  return {
    getState: () => state,
    update(patch) { state = { ...state, ...patch }; listeners.forEach((listener) => listener(state)); },
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
  };
}
