import { useRef, useEffect } from 'react';

export interface BlockData {
    id: string;
    type: 'text' | 'image';
    content?: string;
    src?: string;
    width?: number;
    height?: number;
    styles?: {
        fontSize?: string;
        fontWeight?: string;
        type?: string; // h1, h2, paragraph
    };
}

interface BlockProps {
    block: BlockData;
    onChange: (id: string, content: string) => void;
}

const Block = ({ block, onChange }: BlockProps) => {
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    // Auto-resize textarea
    useEffect(() => {
        if (textareaRef.current) {
            textareaRef.current.style.height = "auto";
            textareaRef.current.style.height = textareaRef.current.scrollHeight + "px";
        }
    }, [block.content]);

    if (block.type === 'image') {
        return (
            <div className="my-4 group relative rounded-lg overflow-hidden shadow-sm hover:shadow-md transition-shadow cursor-default border border-transparent hover:border-blue-200">
                <img
                    src={block.src}
                    alt="PDF Extraction"
                    className="max-w-full h-auto rounded-lg"
                    style={{ maxHeight: '500px', objectFit: 'contain' }}
                />
                <div className="absolute inset-0 bg-black bg-opacity-0 group-hover:bg-opacity-5 transition-all" />
            </div>
        );
    }


    // Text Block
    const fontSizeClass = block.styles?.type === 'h1' ? 'text-3xl font-bold mb-4 mt-6' :
        block.styles?.type === 'h2' ? 'text-xl font-semibold mb-3 mt-4' :
            'text-base mb-2 leading-relaxed';

    return (
        <div className={`w-full group relative transition-all duration-200 rounded-md hover:bg-gray-50 -mx-4 px-4 py-1`}>
            <textarea
                ref={textareaRef}
                value={block.content || ''}
                onChange={(e) => onChange(block.id, e.target.value)}
                className={`w-full bg-transparent resize-none outline-none border-none focus:ring-0 p-0 text-gray-800 ${fontSizeClass}`}
                rows={1}
                placeholder="Type something..."
            />
        </div>
    );
};

export default Block;
