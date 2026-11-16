/**
 * What is known of the panel's page, from the events of its view.
 *
 * A page that fails to load still ends with an error page that Chromium reports as finished, so
 * "it finished" is not "it worked": a load worked only if it stopped without having failed.
 */
export class PanelLoad {
  loaded = false
  loading = false
  private failed = false

  needsLoad(): boolean {
    return !this.loaded && !this.loading
  }

  requested(): void {
    this.loading = true
  }

  started(): void {
    this.failed = false
  }

  failedToLoad(): void {
    this.failed = true
    this.loaded = false
    this.loading = false
  }

  stopped(): void {
    this.loaded = !this.failed
    this.loading = false
  }
}
