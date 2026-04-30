import React, { useRef, useState, useEffect } from 'react';
import EditableBlock, { BlockData } from './EditableBlock';
import { API_BASE } from '../lib/config';

interface PageData {
    index: number;
    width: number;
    height: number;
    blocks: BlockData[];
}

interface PageViewerProps {
    page: PageData;
    fileId: string;
    onBlockChange: (pageIndex: number, blockId: string, updates: Partial<BlockData>) => void;
    selectedBlockId: string | null;
    onSelectBlock: (id: string | null) => void;
}

const PageViewer: React.FC<PageViewerProps> = ({
    page,
    fileId,
    onBlockChange,
    selectedBlockId,
    onSelectBlock
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const [localScale, setLocalScale] = useState(1);

    useEffect(() => {
        const updateScale = () => {
            if (containerRef.current) {
                // page.width is PDF units. container width is pixels.
                setLocalScale(containerRef.current.offsetWidth / page.width);
            }
        };
        updateScale();
        window.addEventListener('resize', updateScale);
        return () => window.removeEventListener('resize', updateScale);
    }, [page.width]);

    // Handle Background Click (Deselect)
    const handleMouseDown = (e: React.MouseEvent) => {
        if (!containerRef.current) return;

        // Clicking background deselects if it wasn't on a block
        if (e.target === containerRef.current || (e.target as HTMLElement).tagName === 'IMG') {
            onSelectBlock(null);
        }
    };

    return (
        <div
            className="relative mb-8 shadow-lg w-full max-w-4xl mx-auto bg-gray-100 select-none"
            ref={containerRef}
            onMouseDown={handleMouseDown}
        >
            <img
                src={`${API_BASE}/pdf/${fileId}/page/${page.index}/image`}
                alt={`Page ${page.index + 1}`}
                className="w-full h-auto block"
                draggable={false}
            />

            <div className="absolute inset-0">
                {page.blocks.map(block => (
                    <EditableBlock
                        key={block.id}
                        block={block}
                        scale={localScale}
                        onChange={(id, updates) => onBlockChange(page.index, id, updates)}
                        isSelected={block.id === selectedBlockId}
                        onSelect={onSelectBlock}
                    />
                ))}
            </div>
        </div>
    );
};

export default PageViewer;
