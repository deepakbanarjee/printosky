import { Dispatch, SetStateAction } from 'react';
import Block, { BlockData } from './Block';

interface EditorProps {
    blocks: BlockData[];
    setBlocks: Dispatch<SetStateAction<BlockData[]>>;
    onReset: () => void;
}

const Editor = ({ blocks, setBlocks, onReset }: EditorProps) => {
    const handleBlockChange = (id: string, newContent: string) => {
        setBlocks((prev: BlockData[]) => prev.map((b: BlockData) => b.id === id ? { ...b, content: newContent } : b));
    };

    return (
        <div className="w-full max-w-4xl mx-auto p-8 bg-white min-h-screen shadow-2xl relative">
            <div className="fixed top-4 right-4 z-50">
                <button
                    onClick={onReset}
                    className="bg-gray-800 text-white px-4 py-2 rounded-full hover:bg-gray-700 shadow-lg transition-all text-sm font-medium"
                >
                    Upload New PDF
                </button>
            </div>

            <div className="flex flex-col space-y-2 pb-20">
                {blocks.map((block: BlockData) => (
                    <Block
                        key={block.id}
                        block={block}
                        onChange={handleBlockChange}
                    />
                ))}
            </div>

            {blocks.length === 0 && (
                <div className="text-center text-gray-400 mt-20">
                    No content to display.
                </div>
            )}
        </div>
    );
};

export default Editor;
