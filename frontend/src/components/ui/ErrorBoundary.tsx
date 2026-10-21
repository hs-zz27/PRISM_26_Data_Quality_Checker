import { Component, ErrorInfo, ReactNode } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { Button } from './Button';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  public override state: State = {
    hasError: false,
    error: null,
  };

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  public override componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error('Uncaught error in UI component:', error, errorInfo);
  }

  public override render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen w-screen bg-base flex items-center justify-center p-6 text-text font-mono">
          <div className="bg-surface border border-status-fail/40 rounded-lg p-6 max-w-lg w-full space-y-4 shadow-xl">
            <div className="flex items-center gap-3 text-status-fail">
              <AlertTriangle className="w-6 h-6 shrink-0" />
              <h2 className="text-base font-semibold">UI Render Failure</h2>
            </div>
            <p className="text-xs text-text-muted">
              An unexpected error occurred while rendering the workspace:
            </p>
            <div className="p-3 bg-base-pure border border-border rounded text-2xs text-status-fail overflow-auto max-h-40 whitespace-pre-wrap">
              {this.state.error?.message ?? 'Unknown render error'}
            </div>
            <div className="pt-2 flex justify-end gap-2">
              <Button
                variant="primary"
                size="sm"
                leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
                onClick={() => window.location.reload()}
              >
                Reload Workstation
              </Button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
