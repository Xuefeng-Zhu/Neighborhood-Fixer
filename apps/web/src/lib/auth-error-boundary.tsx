import { Component } from 'react';
import type { ReactNode } from 'react';
import { SessionScreen } from './session-workspace';
/** This must remain in the entry bundle so it also catches failed auth chunk loads. */
export class AuthErrorBoundary extends Component<
  {
    children: ReactNode;
    onReload?: () => void;
  },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <SessionScreen
        title="Sign-in could not load."
        message="Reload the page to reconnect. Your saved draft is unchanged."
        retryLabel="Reload page"
        retry={this.props.onReload || (() => window.location.reload())}
      />
    ) : (
      this.props.children
    );
  }
}
