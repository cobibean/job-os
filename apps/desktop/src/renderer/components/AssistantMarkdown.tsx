import ReactMarkdown from 'react-markdown'

import { safeExternalUrl } from '../../shared/externalLinks'

interface AssistantMarkdownProps {
  children: string
}

export function AssistantMarkdown({ children }: AssistantMarkdownProps) {
  return (
    <div className="assistant-markdown">
      <ReactMarkdown
        components={{
          a: ({ children: linkText, href, ...props }) => (
            <a
              {...props}
              href={href}
              onClick={event => {
                event.preventDefault()
                if (href) void window.jobos?.shell.openExternal(href)
              }}
              rel="noreferrer noopener"
              target="_blank"
            >
              {linkText}
            </a>
          ),
          img: ({ alt }) => <span className="assistant-markdown-image">{alt ? `[Image: ${alt}]` : '[Image]'}</span>
        }}
        urlTransform={url => safeExternalUrl(url) ?? ''}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}
