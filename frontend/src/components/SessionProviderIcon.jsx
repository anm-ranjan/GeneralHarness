import { useAppSelector } from '../context/AppContext'
import ProviderLogo from './ProviderLogo'
import { providerLabel } from '../usage'

const selectNativeModel = state => state.model

// The logo of the provider a thread last ran with (/model switches update it);
// for Native, the vendor of its model. Hovering names the provider.
export default function SessionProviderIcon({ provider, model = '', className = 'h-3.5 w-3.5' }) {
  const nativeModel = useAppSelector(selectNativeModel)
  const name = provider || 'native'
  const shown = name === 'native' ? model || nativeModel || '' : model
  const title = name === 'native' && shown ? `${providerLabel(name)}: ${shown}` : providerLabel(name)
  return (
    <span className="shrink-0 inline-flex opacity-80" title={title}>
      <ProviderLogo provider={name} model={shown} className={className} />
    </span>
  )
}
