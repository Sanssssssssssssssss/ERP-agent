import {
Business,
BusinessArtifact,
BusinessDetailProjection,
Message
} from './protocol'
import type { BusinessType, BusinessProposal, Material } from '../shared/protocol'


export type ConnectionState = 'checking' | 'connected' | 'disconnected' | 'crashed' | 'protocol_error'

export type BusinessTypeCode = BusinessType

export type MaterialRecord = Omit<Material, 'preview' | 'media_type'> & Partial<Pick<Material, 'preview' | 'media_type'>>

export type MessageWithMaterials = Message & { material_ids?: string[] }

export type BusinessWithType = Omit<Business, 'type'> & { type: BusinessTypeCode | string; completion_target?: string }

export type DetailWithMaterials = BusinessDetailProjection & { materials?: MaterialRecord[] }

export type DownloadReceipt = { status: 'downloading' | 'cancelled' | 'completed' | 'failed'; path?: string; artifact?: BusinessArtifact; error?: string }

export type ApprovalProgress = { key: string; businessId: string; status: 'submitting' | 'failed'; detail?: string }

export type ProposalLike = { id: string; title: string; goal: string; type: string; completion_target?: string; material_ids?: string[]; existing_business_id?: string } & Pick<BusinessProposal, 'source_messages' | 'resolved_references' | 'resolved_release_fields'>
